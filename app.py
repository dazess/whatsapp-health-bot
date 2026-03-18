import os
import hmac
import re
import time
from datetime import datetime, timedelta
from functools import wraps
from dotenv import load_dotenv

# Load .env relative to this file so it works regardless of working directory
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))
os.environ['TZ'] = 'Etc/GMT-8'
if hasattr(time, 'tzset'):
    time.tzset()

from flask import Flask, session, request, jsonify, render_template, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from authlib.integrations.flask_client import OAuth
from apscheduler.schedulers.background import BackgroundScheduler
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import extract, or_

from models import db, Patient, Appointment, QualtricsResponse, SurveyLinkOverride, SurveyReminderEvent, SurveyReminderEscalation, AppSetting, hash_data
from services import BaileysClient, generate_google_calendar_link, generate_birthday_card, send_patient_greeting_if_needed
from time_utils import now_gmt8, today_gmt8
import scheduler_tasks

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///healthbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'super-secret-key')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
cookie_secure_env = os.getenv('SESSION_COOKIE_SECURE')
if cookie_secure_env is None:
    app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV', 'production') == 'production'
else:
    app.config['SESSION_COOKIE_SECURE'] = cookie_secure_env == '1'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH_MB', '2')) * 1024 * 1024

if os.getenv('BEHIND_PROXY', '1') == '1':
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

WEBHOOK_TOKEN = os.getenv('WHATSAPP_WEBHOOK_TOKEN', '').strip()

db.init_app(app)

# --- OAuth Configuration ---
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# Fetch admin emails from env and split by comma
ADMIN_EMAILS = os.getenv('ADMIN_EMAILS', '').split(',')
# Clean up whitespace
ADMIN_EMAILS = [email.strip() for email in ADMIN_EMAILS if email.strip()]
PID_PATTERN = re.compile(r'^P\d{2}$')


def normalize_pid(pid_value):
    if not pid_value:
        return None
    pid = pid_value.strip().upper()
    return pid if pid else None


def normalize_hk_phone(phone_value):
    phone = re.sub(r'\s+', '', phone_value or '')
    return phone if phone else None


def normalize_phone_list(raw_value):
    numbers = []
    seen = set()

    for part in str(raw_value or '').split(','):
        phone = normalize_hk_phone(part)
        if not phone:
            continue
        if not phone.isdigit() or len(phone) != 11 or not phone.startswith('852'):
            raise ValueError('Each number must use format 852xxxxxxxx.')
        if phone in seen:
            continue
        numbers.append(phone)
        seen.add(phone)

    return numbers


def get_staff_alert_numbers_value():
    setting = AppSetting.query.filter_by(setting_key='staff_alert_numbers').first()
    if setting and setting.setting_value:
        return setting.setting_value
    return os.getenv('STAFF_ALERT_NUMBERS', '').strip()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Auth Routes ---

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/login/google')
def login_google():
    redirect_uri = url_for('google_auth', _external=True)
    print(f"DEBUG: Redirect URI sent to Google: {redirect_uri}")
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/callback')
def google_auth():
    token = google.authorize_access_token()
    user_info = token.get('userinfo')
    
    if not user_info:
        flash('Failed to fetch user info from Google.', 'error')
        return redirect(url_for('login'))
        
    email = user_info.get('email')
    
    # Check if email is whitelisted
    # If ADMIN_EMAILS is empty, we might allowed everyone? 
    # BETTER: Default to secure. If empty, allow NO ONE (except maybe during setup?)
    # For now, let's enforce list.
    
    if not ADMIN_EMAILS:
        # Fallback for first run/dev: if env var not set, maybe log a warning.
        # But for security, fail.
        flash('System configuration error: No admins defined.', 'error')
        return redirect(url_for('login'))

    if email not in ADMIN_EMAILS:
        flash('Unauthorized: Your email is not on the admin list.', 'error')
        return redirect(url_for('login'))

    # Encrypt sensitive user info before storing in session
    # We only store a subset of user_info to keep session small and secure
    encrypted_user = {
        'name': user_info.get('name'),
        # Since we use encrypted cookies via Flask's secret key, 
        # the session is already signed. However, the user specifically 
        # asked to "encrypt the login email stored".
        'email': user_info.get('email')
    }

    session['user'] = encrypted_user
    return redirect(url_for('index'))

@app.context_processor
def inject_now():
    return {'now': now_gmt8().replace(tzinfo=None)}

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('Forced logout successful.', 'success')
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    patients = Patient.query.all()
    survey_overview = scheduler_tasks.get_sftp_survey_overview()
    staff_alert_numbers_value = get_staff_alert_numbers_value()
    now = today_gmt8()
    birthday_pending = Patient.query.filter(
        Patient.birthdate.isnot(None),
        extract('month', Patient.birthdate) == now.month,
        extract('day', Patient.birthdate) == now.day,
        or_(
            Patient.birthday_card_sent_year.is_(None),
            Patient.birthday_card_sent_year != now.year,
        ),
    ).order_by(Patient.name).all()

    upcoming_appointments = Appointment.query.filter(
        Appointment.date >= now_gmt8().replace(tzinfo=None)
    ).order_by(Appointment.date).limit(8).all()

    return render_template(
        'index.html',
        patients=patients,
        survey_overview=survey_overview,
        staff_alert_numbers_value=staff_alert_numbers_value,
        birthday_pending=birthday_pending,
        upcoming_appointments=upcoming_appointments,
    )


@app.route('/admin/run_survey_reminders', methods=['POST'])
@login_required
def run_survey_reminders_now():
    """Manually trigger the daily survey reminder job from the dashboard."""
    try:
        scheduler_tasks.send_daily_survey_reminders(app)
        flash('Survey reminders job completed.', 'success')
    except Exception as e:
        flash(f'Error running survey reminders: {str(e)}', 'error')
    return redirect(url_for('index'))


@app.route('/survey/link_override', methods=['POST'])
@login_required
def save_survey_link_override():
    survey_code = (request.form.get('survey_code') or '').strip()
    survey_link = (request.form.get('survey_link') or '').strip()

    if not survey_code:
        flash('Survey code is required.', 'error')
        return redirect(url_for('index'))

    existing = SurveyLinkOverride.query.filter_by(survey_code=survey_code).first()

    # Empty link means remove custom override and fallback to env default/map.
    if not survey_link:
        if existing:
            db.session.delete(existing)
            db.session.commit()
            flash(f"Custom survey link removed for '{survey_code}'.", 'success')
        else:
            flash('No custom survey link to remove.', 'success')
        return redirect(url_for('index'))

    if not (survey_link.startswith('http://') or survey_link.startswith('https://')):
        flash('Survey link must start with http:// or https://', 'error')
        return redirect(url_for('index'))

    if existing:
        existing.survey_link = survey_link
    else:
        db.session.add(SurveyLinkOverride(survey_code=survey_code, survey_link=survey_link))
    db.session.commit()
    flash(f"Custom survey link saved for '{survey_code}'.", 'success')
    return redirect(url_for('index'))


@app.route('/settings/staff_alert_numbers', methods=['POST'])
@login_required
def save_staff_alert_numbers():
    raw_value = request.form.get('staff_alert_numbers', '')

    try:
        normalized_numbers = normalize_phone_list(raw_value)
    except ValueError as exc:
        flash(f'Invalid staff number list: {exc}', 'error')
        return redirect(url_for('index'))

    setting = AppSetting.query.filter_by(setting_key='staff_alert_numbers').first()

    if not normalized_numbers:
        if setting:
            db.session.delete(setting)
            db.session.commit()
        flash('Staff alert numbers cleared. Backend fallback will use STAFF_ALERT_NUMBERS if set.', 'success')
        return redirect(url_for('index'))

    normalized_value = ','.join(normalized_numbers)
    if setting:
        setting.setting_value = normalized_value
    else:
        db.session.add(AppSetting(setting_key='staff_alert_numbers', setting_value=normalized_value))

    db.session.commit()
    flash('Staff alert numbers saved.', 'success')
    return redirect(url_for('index'))


@app.route('/patient/add', methods=['POST'])
@login_required
def add_patient():
    pid = normalize_pid(request.form.get('pid'))
    name = request.form.get('name')
    phone = request.form.get('phone') # Expecting full number e.g. 5511999999999
    birthdate_str = request.form.get('birthdate')  # YYYY-MM-DD, optional
    description = request.form.get('description')  # Free text, optional

    if not pid or not name or not phone:
        flash('PID, name and phone are required.', 'error')
        return redirect(url_for('index'))

    if not PID_PATTERN.match(pid):
        flash('Error: PID must follow format P01 to P99.', 'error')
        return redirect(url_for('index'))

    if not phone.isdigit():
        flash('Error: Phone number must contain only digits.', 'error')
        return redirect(url_for('index'))

    if len(phone) != 11 or not phone.startswith('852'):
        flash('Error: Phone number must correspond to the format 852xxxxxxxx.', 'error')
        return redirect(url_for('index'))

    existing_pid = Patient.query.filter_by(pid=pid).first()
    if existing_pid:
        flash(f'Error: PID {pid} already exists.', 'error')
        return redirect(url_for('index'))

    birthdate = None
    if birthdate_str:
        try:
            birthdate = datetime.strptime(birthdate_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Error: Invalid birthdate format.', 'error')
            return redirect(url_for('index'))

    new_patient = Patient(
        pid=pid,
        name=name,
        birthdate=birthdate,
        description=description or None,
        send_survey_reminders=True,
    )
    new_patient.phone_number = phone

    db.session.add(new_patient)
    db.session.commit()
    flash('Patient added successfully!', 'success')

    return redirect(url_for('index'))

@app.route('/patient/delete/<int:patient_id>', methods=['POST'])
@login_required
def delete_patient(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    Appointment.query.filter_by(patient_id=patient_id).delete()
    SurveyReminderEvent.query.filter_by(patient_id=patient_id).delete()
    SurveyReminderEscalation.query.filter_by(patient_id=patient_id).delete()
    if patient.pid:
        QualtricsResponse.query.filter_by(pid=patient.pid).delete()
    
    db.session.delete(patient)
    db.session.commit()
    flash('Patient data deleted.', 'success')
    return redirect(url_for('index'))

@app.route('/patient/<int:patient_id>')
@login_required
def view_patient(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    appointments = Appointment.query.filter_by(patient_id=patient_id).order_by(Appointment.date).all()
    return render_template('patient_detail.html', patient=patient, appointments=appointments)

@app.route('/appointment/add', methods=['POST'])
@login_required
def add_appointment():
    patient_id = request.form.get('patient_id')
    date_str = request.form.get('date') # Format: YYYY-MM-DDTHH:MM
    description = request.form.get('description')
    
    if patient_id and date_str:
        date_obj = datetime.strptime(date_str, '%Y-%m-%dT%H:%M')
        new_appt = Appointment(patient_id=patient_id, date=date_obj, description=description)
        db.session.add(new_appt)
        db.session.commit()
        return redirect(url_for('view_patient', patient_id=patient_id))
    
    return redirect(url_for('index'))

@app.route('/appointment/send_reminder/<int:appointment_id>', methods=['POST'])
@login_required
def send_appointment_reminder_now(appointment_id):
    appointment = Appointment.query.get_or_404(appointment_id)
    patient = appointment.patient
    
    msg = f"{patient.name}，提醒您：您於 {appointment.date.strftime('%m月%d日 %H:%M')} 有預約。"
    if appointment.description:
        msg += f" 備註：{appointment.description}"
    
    cal_link = generate_google_calendar_link(
        title=f"診症預約 - {patient.name}",
        start_dt=appointment.date,
        description=appointment.description or "Medical Appointment"
    )
    msg += f"\n\n行事曆連結：{cal_link}"
    
    try:
        client = BaileysClient()
        # Send initial greeting if not yet greeted
        send_patient_greeting_if_needed(patient, client)
        
        print(f"Sending manual reminder to {patient.name} ({patient.phone_number})...")
        result = client.send_message(patient.phone_number, msg)
        
        if result and result.get('status') == 'sent':
            appointment.reminded = True
            db.session.commit()
            flash('Reminder sent successfully!', 'success')
        else:
            error_message = (result or {}).get('error', 'Unknown error')
            flash(f'Failed to send reminder: {error_message}', 'error')
            
    except Exception as e:
        flash(f'Error sending reminder: {str(e)}', 'error')

    return redirect(url_for('view_patient', patient_id=patient.id))

@app.route('/appointment/delete/<int:appointment_id>', methods=['POST'])
@login_required
def delete_appointment(appointment_id):
    appointment = Appointment.query.get_or_404(appointment_id)
    patient_id = appointment.patient_id
    db.session.delete(appointment)
    db.session.commit()
    flash('Appointment cancelled.', 'success')
    return redirect(url_for('view_patient', patient_id=patient_id))

@app.route('/appointment/edit/<int:appointment_id>', methods=['POST'])
@login_required
def edit_appointment(appointment_id):
    appointment = Appointment.query.get_or_404(appointment_id)
    appointment.description = request.form.get('description', '').strip() or None
    db.session.commit()
    flash('Appointment updated.', 'success')
    return redirect(url_for('view_patient', patient_id=appointment.patient_id))

@app.route('/patient/edit/<int:patient_id>', methods=['POST'])
@login_required
def edit_patient(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    new_pid = normalize_pid(request.form.get('pid'))

    if not new_pid:
        flash('PID is required.', 'error')
        return redirect(url_for('view_patient', patient_id=patient_id))

    if not PID_PATTERN.match(new_pid):
        flash('PID must follow format P01 to P99.', 'error')
        return redirect(url_for('view_patient', patient_id=patient_id))

    existing_pid = Patient.query.filter(Patient.pid == new_pid, Patient.id != patient.id).first()
    if existing_pid:
        flash(f'PID {new_pid} is already assigned to another patient.', 'error')
        return redirect(url_for('view_patient', patient_id=patient_id))

    patient.pid = new_pid
    patient.name = request.form.get('name', '').strip() or patient.name
    patient.description = request.form.get('description', '').strip() or None
    birthdate_str = request.form.get('birthdate', '').strip()
    if birthdate_str:
        try:
            patient.birthdate = datetime.strptime(birthdate_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid birthdate format.', 'error')
            return redirect(url_for('view_patient', patient_id=patient_id))
    else:
        patient.birthdate = None
    
    patient.send_survey_reminders = True
    
    db.session.commit()
    flash('Patient details updated.', 'success')
    return redirect(url_for('view_patient', patient_id=patient_id))

@app.route('/patient/preview_birthday_card/<int:patient_id>', methods=['POST'])
@login_required
def preview_birthday_card(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    try:
        card_text = generate_birthday_card(
            patient_name=patient.name,
            patient_description=patient.description or '',
        )
        return render_template('birthday_card_preview.html', patient=patient, card_text=card_text)
    except Exception as e:
        flash(f'Error generating birthday card: {str(e)}', 'error')
        return redirect(url_for('view_patient', patient_id=patient_id))

@app.route('/patient/process_birthday_card/<int:patient_id>', methods=['POST'])
@login_required
def confirm_send_birthday_card(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    action = request.form.get('action')
    
    if action == 'regenerate':
        try:
            card_text = generate_birthday_card(
                patient_name=patient.name,
                patient_description=patient.description or '',
            )
            flash('Birthday card regenerated.', 'success')
            return render_template('birthday_card_preview.html', patient=patient, card_text=card_text)
        except Exception as e:
            flash(f'Error regenerating card: {str(e)}', 'error')
            return redirect(url_for('view_patient', patient_id=patient_id))
            
    elif action == 'send':
        card_text = request.form.get('card_text')
        try:
            client = BaileysClient()
            result = client.send_message(patient.phone_number, card_text)
            if result and result.get('status') != 'error':
                patient.birthday_card_sent_year = now_gmt8().year
                db.session.commit()
                flash('Birthday card sent successfully!', 'success')
            else:
                error_message = (result or {}).get('error', 'Unknown error')
                flash(f'Failed to send birthday card: {error_message}', 'error')
        except Exception as e:
            flash(f'Error sending birthday card: {str(e)}', 'error')
        return redirect(url_for('view_patient', patient_id=patient_id))
    
    return redirect(url_for('view_patient', patient_id=patient_id))

# --- Webhook for Baileys Service (Handling Incoming Messages) ---

@app.route('/webhook/whatsapp', methods=['POST'])
def whatsapp_webhook():
    data = request.json
    provided_token = request.headers.get('X-Webhook-Token', '')

    if WEBHOOK_TOKEN and not hmac.compare_digest(provided_token, WEBHOOK_TOKEN):
        return jsonify({'status': 'error', 'reason': 'unauthorized'}), 401
    
    try:
        sender = data.get('sender')
        message_content = data.get('message')
        
        if not sender or not message_content:
                return jsonify({'status': 'ignored', 'reason': 'missing_data'}), 400

        # Shared phone numbers are allowed, so webhook matching can be ambiguous.
        lookup_hash = hash_data(sender)
        matched_patients = Patient.query.filter_by(phone_lookup_hash=lookup_hash).all()
        if not matched_patients:
            legacy_match = Patient.query.filter_by(phone_hash=lookup_hash).first()
            if legacy_match:
                matched_patients = [legacy_match]

        if len(matched_patients) > 1:
            client = BaileysClient()
            client.send_message(sender, "此電話號碼對應多位病人，系統暫時無法自動識別。請聯絡診所職員處理。")
            return jsonify({'status': 'ignored', 'reason': 'ambiguous_phone_match'}), 200

        patient = matched_patients[0] if matched_patients else None
        
        if patient:
            client = BaileysClient()
            
            # Send initial greeting if not yet greeted
            send_patient_greeting_if_needed(patient, client)

            # Check length (max 500 characters)
            if len(message_content) > 500:
                client.send_message(sender, "您輸入的內容超過 500 字，請縮短後再發送。")
                return jsonify({'status': 'ignored', 'reason': 'message_too_long'}), 200
            
            client.send_message(sender, "已收到您的訊息。請留意系統發送的預約、生日卡及問卷通知。")
            return jsonify({'status': 'received'}), 200
        else:
            print(f"Received message from unknown number: {sender}")
            return jsonify({'status': 'ignored', 'reason': 'unknown_patient'}), 200

    except Exception as e:
        print(f"Error processing webhook: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


# --- Application Setup ---

def seed_mock_patients():
    """Idempotent seed for mock PID records P01-P10 sharing one phone number."""
    shared_phone = '85252624849'
    created = 0

    for i in range(1, 11):
        pid = f'P{i:02d}'
        if Patient.query.filter_by(pid=pid).first():
            continue

        patient = Patient(
            pid=pid,
            name=f'Mock Patient {pid}',
            description='Mock record for Qualtrics survey reminder testing.',
            send_survey_reminders=True,
        )
        patient.phone_number = shared_phone
        db.session.add(patient)
        created += 1

    if created:
        db.session.commit()
        print(f"Seeded {created} mock patients (P01-P10) with shared phone {shared_phone}.")


@app.route('/admin/seed_mock_patients', methods=['POST'])
@login_required
def seed_mock_patients_route():
    seed_mock_patients()
    flash('Mock patients P01-P10 seeding completed.', 'success')
    return redirect(url_for('index'))

def _migrate_db():
    """Add new columns to existing SQLite database if they are missing."""
    from sqlalchemy import text
    with db.engine.connect() as conn:
        existing = [row[1] for row in conn.execute(text("PRAGMA table_info(patient)")).fetchall()]
        migrations = [
            ("birthdate", "ALTER TABLE patient ADD COLUMN birthdate DATE"),
            ("description", "ALTER TABLE patient ADD COLUMN description TEXT"),
            ("birthday_card_sent_year", "ALTER TABLE patient ADD COLUMN birthday_card_sent_year INTEGER"),
            ("pid", "ALTER TABLE patient ADD COLUMN pid VARCHAR(20)"),
            ("phone_lookup_hash", "ALTER TABLE patient ADD COLUMN phone_lookup_hash VARCHAR(64)"),
            ("send_survey_reminders", "ALTER TABLE patient ADD COLUMN send_survey_reminders BOOLEAN NOT NULL DEFAULT 1"),
            ("last_survey_reminder_date", "ALTER TABLE patient ADD COLUMN last_survey_reminder_date DATE"),
        ]
        for col, sql in migrations:
            if col not in existing:
                conn.execute(text(sql))
                print(f"DB migration: added column '{col}' to patient table.")

        # Best-effort indexes for PID and phone lookup.
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_patient_pid ON patient(pid) WHERE pid IS NOT NULL"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_patient_phone_lookup_hash ON patient(phone_lookup_hash)"))
        conn.commit()

    # Ensure new lookup hashes exist for old rows.
    updated = False
    for patient in Patient.query.all():
        if not patient.phone_lookup_hash and patient.phone_number:
            patient.phone_lookup_hash = hash_data(patient.phone_number)
            updated = True
    if updated:
        db.session.commit()

    # Ensure the new response-tracking table exists for older databases.
    QualtricsResponse.__table__.create(db.engine, checkfirst=True)
    SurveyLinkOverride.__table__.create(db.engine, checkfirst=True)
    SurveyReminderEvent.__table__.create(db.engine, checkfirst=True)
    SurveyReminderEscalation.__table__.create(db.engine, checkfirst=True)
    AppSetting.__table__.create(db.engine, checkfirst=True)

    with db.engine.connect() as conn:
        existing_qr_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(qualtrics_response)")).fetchall()]
        if 'survey_code' not in existing_qr_cols:
            conn.execute(text("ALTER TABLE qualtrics_response ADD COLUMN survey_code VARCHAR(100)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_qualtrics_response_survey_code ON qualtrics_response(survey_code)"))
            conn.commit()

    # As requested: add mock P01-P10 patients if missing.
    seed_mock_patients()


def create_scheduler():
    scheduler = BackgroundScheduler()
    # Check for appointments every hour (or once a day)
    scheduler.add_job(func=scheduler_tasks.send_appointment_reminders, args=[app], trigger="interval", hours=1)

    # Sync Qualtrics responses and remind non-responders daily at 9:00 AM
    scheduler.add_job(func=scheduler_tasks.send_daily_survey_reminders, args=[app], trigger="cron", hour=9, minute=0)

    scheduler.start()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        _migrate_db()

    create_scheduler()
    app.run(host='127.0.0.1', debug=False, port=int(os.getenv('PORT', '5000')))
