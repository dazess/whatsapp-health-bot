from datetime import datetime, timedelta
import csv
import json
import os
import re
from pathlib import Path

from models import db, Patient, Appointment, QualtricsResponse, SurveyLinkOverride, SurveyReminderEvent, SurveyReminderEscalation, AppSetting
from services import BaileysClient, generate_google_calendar_link, send_patient_greeting_if_needed
from time_utils import today_gmt8, now_gmt8_naive


DEFAULT_SFTP_UPLOAD_DIR = '/home/qualtricssftp/uploads'
MAX_SURVEY_REMINDERS_PER_PATIENT = 7


def send_appointment_reminders(app):
    """Checks for appointments scheduled for tomorrow and sends reminders."""
    with app.app_context():
        tomorrow = today_gmt8() + timedelta(days=1)
        start_of_day = datetime.combine(tomorrow, datetime.min.time())
        end_of_day = datetime.combine(tomorrow, datetime.max.time())

        appointments = Appointment.query.filter(
            Appointment.date >= start_of_day,
            Appointment.date <= end_of_day,
            Appointment.reminded == False,
        ).all()

        client = BaileysClient()

        for appointment in appointments:
            patient = appointment.patient
            send_patient_greeting_if_needed(patient, client)
            msg = f"{patient.name}，提醒您：您明天 {appointment.date.strftime('%H:%M')} 有預約。"

            cal_link = generate_google_calendar_link(
                title=f"診症預約 - {patient.name}",
                start_dt=appointment.date,
                description=appointment.description or 'Clinic appointment',
            )
            msg += f"\n\n行事曆連結：{cal_link}"

            result = client.send_message(patient.phone_number, msg)
            if result and result.get('status') == 'sent':
                appointment.reminded = True
                db.session.commit()


def _get_sftp_upload_dir():
    return Path(os.getenv('SFTP_UPLOAD_DIR', DEFAULT_SFTP_UPLOAD_DIR)).expanduser()


def _normalize_phone_list(raw_value):
    numbers = []
    seen = set()
    for part in str(raw_value or '').split(','):
        phone = re.sub(r'\s+', '', part)
        if not phone or phone in seen:
            continue
        if phone.isdigit() and len(phone) == 11 and phone.startswith('852'):
            numbers.append(phone)
            seen.add(phone)
    return numbers


def _get_staff_alert_numbers():
    setting = AppSetting.query.filter_by(setting_key='staff_alert_numbers').first()
    if setting and setting.setting_value:
        return _normalize_phone_list(setting.setting_value)
    return _normalize_phone_list(os.getenv('STAFF_ALERT_NUMBERS', ''))


def _alert_staff_for_stalled_patient(client, patient, survey_code, reminder_count, staff_numbers):
    if not staff_numbers:
        print(
            f"Survey escalation pending for patient_id={patient.id}, survey='{survey_code}' but no staff alert numbers are configured."
        )
        return []

    message = (
        "[問卷提醒升級通知]\n"
        f"病人：{patient.name}（{patient.pid or '未設定 PID'}）\n"
        f"問卷：{survey_code}\n"
        f"提醒次數：{reminder_count}（已停止向病人發送）\n"
        f"聯絡電話：{patient.phone_number}\n"
        "請由診所職員跟進。"
    )

    successful_numbers = []
    for phone in staff_numbers:
        result = client.send_message(phone, message)
        if result and result.get('status') != 'error':
            successful_numbers.append(phone)

    return successful_numbers


def _survey_code_from_filename(file_path):
    stem = Path(file_path).stem
    # Remove common Qualtrics timestamp suffix: _March 15, 2026_18.05
    stem = re.sub(r'_[A-Za-z]+\s+\d{1,2},\s+\d{4}_\d{1,2}\.\d{2}$', '', stem)
    return stem.strip() or Path(file_path).stem


def _has_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]', text or ''))


def _normalize_survey_heading(raw_heading):
    """Normalize heading text into a stable display/grouping key."""
    if not raw_heading:
        return None

    text = str(raw_heading).replace('\r', '\n')
    text = re.sub(r'\s+', ' ', text).strip()

    # Keep primary Chinese heading, drop long bilingual instruction tails.
    text = re.split(r'(Subthreshold|Food Allergy Quality of Life Questionnaire|We hope to understand|Please answer)', text, maxsplit=1)[0]
    text = text.strip(' -:：')

    if _has_chinese(text):
        m = re.search(r'(.{0,180}?問卷[^\n]*)', text)
        if m:
            text = m.group(1).strip(' -:：')

    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > 180:
        text = text[:180].rstrip()
    return text or None


def _extract_survey_heading_from_label_rows(rows):
    """Extract Chinese survey heading from Qualtrics label/header metadata rows."""
    if not rows:
        return None

    candidates = []
    for row in rows[:3]:
        for value in row.values():
            if not isinstance(value, str):
                continue
            v = value.strip()
            if not v or v.startswith('{'):
                continue
            if _has_chinese(v):
                candidates.append(v)

    if not candidates:
        return None

    # Longest Chinese metadata cell usually contains the full survey title block.
    raw = max(candidates, key=len)
    # Try to pick a meaningful line from multi-line text.
    lines = [ln.strip() for ln in raw.replace('\r', '\n').split('\n') if ln.strip()]
    for line in lines:
        if _has_chinese(line) and not re.match(r'^Q\d+', line):
            normalized = _normalize_survey_heading(line)
            if normalized:
                return normalized

    return _normalize_survey_heading(raw)


def _list_sftp_csv_files():
    upload_dir = _get_sftp_upload_dir()
    if not upload_dir.exists() or not upload_dir.is_dir():
        return []
    return sorted(upload_dir.glob('*.csv'), key=lambda p: p.stat().st_mtime)


def _iter_qualtrics_data_rows(file_path):
    with open(file_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    heading = _extract_survey_heading_from_label_rows(rows)

    skip = 0
    for i, row in enumerate(rows[:3]):
        first_val = next(iter(row.values()), '')
        if isinstance(first_val, str) and first_val.startswith('{'):
            skip = i + 1
            break

    return heading, rows[skip:]


def _resolve_survey_code(file_path, heading):
    """Prefer Chinese heading for grouping; fallback to normalized filename."""
    normalized_heading = _normalize_survey_heading(heading)
    if normalized_heading:
        return normalized_heading
    return _survey_code_from_filename(file_path)


def _extract_pid(row):
    candidates = (
        'Q1_6',
        'QID1_6',
        'PID',
        'pid',
    )
    pid_raw = None
    for key in candidates:
        if key in row and row.get(key):
            pid_raw = row.get(key)
            break

    if pid_raw is None:
        for key, val in row.items():
            if key and key.lower() in ('q1_6', 'qid1_6', 'pid') and val:
                pid_raw = val
                break

    if not pid_raw:
        return None

    pid = str(pid_raw).strip().upper()
    return pid or None


def _extract_response_id(row, fallback):
    raw = (
        row.get('ResponseId')
        or row.get('responseId')
        or row.get('_recordId')
        or row.get('response_id')
        or fallback
    )
    return str(raw).strip()


def _extract_recorded_at(row):
    for key in ('RecordedDate', 'recordedDate', 'EndDate', 'endDate'):
        raw = row.get(key)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        except ValueError:
            continue
    return None


def _get_survey_link_config():
    default_link = os.getenv('QUALTRICS_SURVEY_LINK', '').strip()
    raw_map = os.getenv('SURVEY_LINK_MAP_JSON', '').strip()
    link_map = {}

    if raw_map:
        try:
            data = json.loads(raw_map)
            if isinstance(data, dict):
                link_map = {str(k).strip(): str(v).strip() for k, v in data.items() if str(v).strip()}
        except json.JSONDecodeError:
            print('SURVEY_LINK_MAP_JSON is invalid JSON. Falling back to default survey link.')

    # Per-survey overrides from dashboard (highest priority)
    for override in SurveyLinkOverride.query.all():
        key = (override.survey_code or '').strip()
        val = (override.survey_link or '').strip()
        if key and val:
            link_map[key] = val

    return link_map, default_link


def get_sftp_survey_overview():
    """Build dashboard survey summary directly from SFTP CSV exports."""
    files = _list_sftp_csv_files()
    grouped = {}

    for file_path in files:
        file_key = _survey_code_from_filename(file_path)
        heading, _ = _iter_qualtrics_data_rows(file_path)
        code = _resolve_survey_code(file_path, heading)
        grouped.setdefault(code, {
            'survey_code': code,
            'file_count': 0,
            'latest_file': None,
            'source_file_key': file_key,
        })
        grouped[code]['file_count'] += 1
        grouped[code]['latest_file'] = file_path.name

    link_map, default_link = _get_survey_link_config()

    overview = []
    for code in sorted(grouped.keys()):
        response_count = QualtricsResponse.query.filter_by(survey_code=code).count()
        overview.append({
            'survey_code': code,
            'source_file_key': grouped[code]['source_file_key'],
            'file_count': grouped[code]['file_count'],
            'latest_file': grouped[code]['latest_file'],
            'response_count': response_count,
            'survey_link': link_map.get(code) or default_link,
        })

    return overview


def sync_sftp_responses():
    """Ingest all CSV files from the SFTP upload folder into QualtricsResponse."""
    files = _list_sftp_csv_files()
    synced = 0
    skipped = 0
    survey_codes = set()
    now = now_gmt8_naive()

    for file_path in files:
        heading, rows = _iter_qualtrics_data_rows(file_path)
        survey_code = _resolve_survey_code(file_path, heading)
        survey_codes.add(survey_code)

        for i, row in enumerate(rows):
            pid = _extract_pid(row)
            if not pid:
                skipped += 1
                continue

            response_id_raw = _extract_response_id(row, fallback=f'{file_path.name}:{i}')
            response_id = f'{survey_code}:{response_id_raw}'
            recorded_at = _extract_recorded_at(row)

            existing = QualtricsResponse.query.filter_by(qualtrics_response_id=response_id).first()
            if existing:
                existing.survey_code = survey_code
                existing.pid = pid
                existing.recorded_at = recorded_at or existing.recorded_at
                existing.last_seen_at = now
            else:
                db.session.add(
                    QualtricsResponse(
                        survey_code=survey_code,
                        pid=pid,
                        qualtrics_response_id=response_id,
                        recorded_at=recorded_at,
                        last_seen_at=now,
                    )
                )
            synced += 1

    if synced:
        db.session.commit()

    return {
        'synced': synced,
        'skipped': skipped,
        'survey_codes': sorted(survey_codes),
    }


def send_daily_survey_reminders(app):
    """
    Daily job: read SFTP CSV exports, identify surveys by filename, send
    reminders per survey to non-responders, stop after 7 unanswered attempts,
    and escalate to staff over WhatsApp once.
    """
    with app.app_context():
        sync_result = sync_sftp_responses()
        survey_codes = sync_result['survey_codes']

        if not survey_codes:
            print('Survey reminder skipped: no SFTP CSV surveys found.')
            return

        link_map, default_link = _get_survey_link_config()
        today = today_gmt8()
        patients = Patient.query.filter(
            Patient.send_survey_reminders == True,
            Patient.pid.isnot(None),
        ).all()
        staff_alert_numbers = _get_staff_alert_numbers()

        client = BaileysClient()
        total_sent = 0
        total_escalated = 0

        for survey_code in survey_codes:
            responded_pids = {
                row.pid.strip().upper()
                for row in QualtricsResponse.query.filter_by(survey_code=survey_code).with_entities(QualtricsResponse.pid).all()
                if row.pid
            }

            survey_link = link_map.get(survey_code) or default_link
            sent_count = 0

            for patient in patients:
                normalized_pid = patient.pid.strip().upper() if patient.pid else None
                if not normalized_pid:
                    continue

                if normalized_pid in responded_pids:
                    continue

                already_sent_today = SurveyReminderEvent.query.filter_by(
                    patient_id=patient.id,
                    survey_code=survey_code,
                    sent_date=today,
                ).first()
                if already_sent_today:
                    continue

                total_previous = SurveyReminderEvent.query.filter_by(
                    patient_id=patient.id,
                    survey_code=survey_code,
                ).count()
                if total_previous >= MAX_SURVEY_REMINDERS_PER_PATIENT:
                    existing_escalation = SurveyReminderEscalation.query.filter_by(
                        patient_id=patient.id,
                        survey_code=survey_code,
                    ).first()
                    if not existing_escalation:
                        successful_numbers = _alert_staff_for_stalled_patient(
                            client,
                            patient,
                            survey_code,
                            total_previous,
                            staff_alert_numbers,
                        )
                        if successful_numbers:
                            db.session.add(
                                SurveyReminderEscalation(
                                    patient_id=patient.id,
                                    survey_code=survey_code,
                                    reminder_count=total_previous,
                                    recipients=','.join(successful_numbers),
                                )
                            )
                            db.session.commit()
                            total_escalated += 1
                    continue

                send_patient_greeting_if_needed(patient, client)
                message = f"{patient.name}，請填寫以下問卷。\n"
                if survey_link:
                    message += f"\n問卷連結：{survey_link}"

                result = client.send_message(patient.phone_number, message)
                if result and result.get('status') != 'error':
                    db.session.add(
                        SurveyReminderEvent(
                            patient_id=patient.id,
                            survey_code=survey_code,
                            sent_date=today,
                        )
                    )
                    db.session.commit()
                    sent_count += 1

            total_sent += sent_count
            print(f"Survey '{survey_code}' reminder run complete. sent={sent_count}")

        print(
            f"SFTP survey reminder run complete. synced={sync_result['synced']}, "
            f"skipped={sync_result['skipped']}, sent={total_sent}, escalated={total_escalated}"
        )
