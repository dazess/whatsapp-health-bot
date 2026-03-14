from datetime import datetime, timedelta
import os
from models import db, Patient, Appointment, QualtricsResponse, SurveyLink, SurveyReminderLog
from services import BaileysClient, QualtricsClient, generate_google_calendar_link, generate_birthday_card, send_patient_greeting_if_needed

def send_appointment_reminders(app):
    """
    Checks for appointments scheduled for tomorrow and sends reminders.
    """
    with app.app_context():
        # Calculate date for tomorrow
        tomorrow = datetime.now().date() + timedelta(days=1)
        start_of_day = datetime.combine(tomorrow, datetime.min.time())
        end_of_day = datetime.combine(tomorrow, datetime.max.time())
        
        # Find appointments for tomorrow that haven't been reminded
        appointments = Appointment.query.filter(
            Appointment.date >= start_of_day,
            Appointment.date <= end_of_day,
            Appointment.reminded == False
        ).all()
        
        client = BaileysClient()
        
        for appointment in appointments:
            patient = appointment.patient            # Send initial greeting if not yet greeted
            send_patient_greeting_if_needed(patient, client)
            msg = f"Hello {patient.name} 小朋友，溫馨提示：聽日 {appointment.date.strftime('%H:%M')} 有預約。"
            
            cal_link = generate_google_calendar_link(
                title=f"醫務覆診 - {patient.name}",
                start_dt=appointment.date,
                description=appointment.description or "Medical Appointment"
            )
            msg += f"\n\n加落Google Calendar: {cal_link}"
            
            print(f"Sending reminder to {patient.name} ({patient.phone_number})...")
            result = client.send_message(patient.phone_number, msg)
            
            if result and result.get('status') == 'sent':
                appointment.reminded = True
                db.session.commit()

def send_daily_diary_reminders(app):
    """
    Sends a daily reminder to patients who have e-diary reminders enabled.
    """
    with app.app_context():
        # Only send to patients with send_ediary_reminders=True
        patients = Patient.query.filter_by(send_ediary_reminders=True).all()
        client = BaileysClient()

        msg = "晚安！記得今日要填 e-diary 呀。直接喺度回覆就得。記得用心情、症狀、活動等資訊，幫助醫生更了解你嘅情況！"

        for patient in patients:
            # Send initial greeting if not yet greeted
            send_patient_greeting_if_needed(patient, client)
            
            print(f"Sending diary reminder to {patient.name}...")
            client.send_message(patient.phone_number, msg)


def send_birthday_cards(app):
    """
    Runs daily. Finds patients whose birthday is today, generates an AI-powered
    Cantonese birthday card, and sends it via WhatsApp (once per calendar year).
    """
    with app.app_context():
        today = datetime.now().date()
        current_year = today.year

        # Fetch all patients that have a birthdate set
        patients = Patient.query.filter(Patient.birthdate.isnot(None)).all()
        client = BaileysClient()

        for patient in patients:
            bd = patient.birthdate
            # Match month and day regardless of year
            if bd.month != today.month or bd.day != today.day:
                continue

            # Skip if we already sent this year
            if patient.birthday_card_sent_year == current_year:
                print(f"Birthday card already sent to {patient.name} in {current_year}, skipping.")
                continue

            print(f"Generating birthday card for {patient.name}...")
            card_text = generate_birthday_card(
                patient_name=patient.name,
                patient_description=patient.description or '',
            )

            result = client.send_message(patient.phone_number, card_text)
            if result and result.get('status') != 'error':
                patient.birthday_card_sent_year = current_year
                db.session.commit()
                print(f"Birthday card sent to {patient.name}.")
            else:
                print(f"Failed to send birthday card to {patient.name}: {result}")


def sync_qualtrics_responses(app, survey):
    """Sync Qualtrics responses for one survey link into local tracking table."""
    with app.app_context():
        client = QualtricsClient()
        if not client.is_configured():
            print('Qualtrics sync skipped: missing QUALTRICS_* configuration.')
            return {
                'synced': 0,
                'unknown_pid': 0,
            }

        if not survey.qualtrics_survey_id:
            return {
                'synced': 0,
                'unknown_pid': 0,
            }

        items = client.fetch_responses(survey_id=survey.qualtrics_survey_id)
        synced = 0
        unknown_pid = 0
        now = datetime.utcnow()

        for item in items:
            pid = client.extract_pid(item, pid_field_override=survey.pid_field)
            if not pid:
                unknown_pid += 1
                continue

            source_id = item.get('responseId') or item.get('id')
            response_id = (
                f"{survey.qualtrics_survey_id}:{source_id}"
                if source_id
                else f"{survey.qualtrics_survey_id}:{pid}-{item.get('recordedDate') or now.isoformat()}"
            )
            recorded_at_raw = item.get('recordedDate')
            recorded_at = None
            if recorded_at_raw:
                # Handles ISO timestamps that may end with Z.
                safe_ts = str(recorded_at_raw).replace('Z', '+00:00')
                try:
                    recorded_at = datetime.fromisoformat(safe_ts)
                except ValueError:
                    recorded_at = None

            existing = QualtricsResponse.query.filter_by(qualtrics_response_id=response_id).first()
            if existing:
                existing.survey_code = survey.qualtrics_survey_id
                existing.pid = pid
                existing.recorded_at = recorded_at or existing.recorded_at
                existing.last_seen_at = now
            else:
                db.session.add(
                    QualtricsResponse(
                        survey_code=survey.qualtrics_survey_id,
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
            'unknown_pid': unknown_pid,
        }


def send_daily_survey_reminders(app):
    """
    Daily job: sync Qualtrics responses, compare submitted PIDs with local
    patients, and send Cantonese reminders to non-responders.
    """
    with app.app_context():
        active_surveys = SurveyLink.query.filter_by(is_active=True).all()
        if not active_surveys:
            fallback_link = os.getenv('QUALTRICS_SURVEY_LINK', '').strip()
            if fallback_link:
                active_surveys = [
                    SurveyLink(
                        id=0,
                        title='Default Survey',
                        url=fallback_link,
                        qualtrics_survey_id=os.getenv('QUALTRICS_SURVEY_ID', '').strip() or None,
                        pid_field=os.getenv('QUALTRICS_PID_FIELD', 'PID').strip() or 'PID',
                        is_active=True,
                    )
                ]

        if not active_surveys:
            print('Survey reminder skipped: no active surveys configured.')
            return

        today = datetime.now().date()
        patients = Patient.query.filter(
            Patient.send_survey_reminders == True,
            Patient.pid.isnot(None),
        ).all()
        total_sent = 0
        client = BaileysClient()

        for survey in active_surveys:
            sync_result = sync_qualtrics_responses(app, survey)
            # Use a consistent survey_code: prefer the Qualtrics Survey ID; fall back to
            # 'survey-{id}' so that manually uploaded CSVs (which use the same fallback)
            # are correctly matched even when no Qualtrics Survey ID is configured.
            survey_code = survey.qualtrics_survey_id or (f'survey-{survey.id}' if survey.id is not None else None)
            responded_pids = set()
            if survey_code:
                responded_pids = {
                    row.pid.strip().upper()
                    for row in QualtricsResponse.query.filter_by(survey_code=survey_code).with_entities(QualtricsResponse.pid).all()
                    if row.pid
                }

            reminder_message = (
                f"你好！溫馨提示：請今日填寫健康問卷（{survey.title}），幫我哋更了解你而家嘅情況。\n"
                f"問卷連結：{survey.url}\n"
                "多謝你合作！"
            )

            sent_count = 0
            for patient in patients:
                normalized_pid = patient.pid.strip().upper() if patient.pid else None
                if not normalized_pid:
                    continue

                if normalized_pid in responded_pids:
                    continue

                if survey.id:
                    already_sent = SurveyReminderLog.query.filter_by(
                        patient_id=patient.id,
                        survey_link_id=survey.id,
                        sent_date=today,
                    ).first()
                    if already_sent:
                        continue

                send_patient_greeting_if_needed(patient, client)
                result = client.send_message(patient.phone_number, reminder_message)
                if result and result.get('status') != 'error':
                    patient.last_survey_reminder_date = today
                    if survey.id:
                        db.session.add(SurveyReminderLog(patient_id=patient.id, survey_link_id=survey.id, sent_date=today))
                    db.session.commit()
                    sent_count += 1

            total_sent += sent_count
            print(
                f"Survey '{survey.title}' reminder run complete. synced={sync_result['synced']}, "
                f"unknown_pid={sync_result['unknown_pid']}, sent={sent_count}"
            )

        print(f"All survey reminder runs complete. total_sent={total_sent}")
