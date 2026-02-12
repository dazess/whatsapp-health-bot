import os
import hmac
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, session, request, jsonify, render_template, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from authlib.integrations.flask_client import OAuth
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

from models import db, Patient, Appointment, DiaryEntry, hash_data
from services import BaileysClient, generate_google_calendar_link
import scheduler_tasks

load_dotenv()

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

    session['user'] = user_info
    return redirect(url_for('index'))

@app.context_processor
def inject_now():
    return {'now': datetime.now()}

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash('Forced logout successful.', 'success')
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    patients = Patient.query.all()
    # Also fetch upcoming appointments for display if needed
    return render_template('index.html', patients=patients)

@app.route('/patient/add', methods=['POST'])
@login_required
def add_patient():
    name = request.form.get('name')
    phone = request.form.get('phone') # Expecting full number e.g. 5511999999999
    
    if not name or not phone:
        flash('Name and phone are required.', 'error')
        return redirect(url_for('index'))

    if not phone.isdigit():
        flash('Error: Phone number must contain only digits.', 'error')
        return redirect(url_for('index'))
    
    if len(phone) != 11 or not phone.startswith('852'):
        flash('Error: Phone number must correspond to the format 852xxxxxxxx.', 'error')
        return redirect(url_for('index'))

    new_patient = Patient(name=name, phone_number=phone)
    db.session.add(new_patient)
    db.session.commit()
    flash('Patient added successfully!', 'success')
    
    return redirect(url_for('index'))

@app.route('/patient/delete/<int:patient_id>', methods=['POST'])
@login_required
def delete_patient(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    # Delete related records manually if cascade is not set up in models (simple deletion for now)
    # SQLAlchemy default relationship might not cascade delete if not configured
    Appointment.query.filter_by(patient_id=patient_id).delete()
    DiaryEntry.query.filter_by(patient_id=patient_id).delete()
    
    db.session.delete(patient)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/patient/<int:patient_id>')
@login_required
def view_patient(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    appointments = Appointment.query.filter_by(patient_id=patient_id).order_by(Appointment.date).all()
    diary_entries = DiaryEntry.query.filter_by(patient_id=patient_id).order_by(DiaryEntry.date.desc()).all()
    return render_template('patient_detail.html', patient=patient, appointments=appointments, diary_entries=diary_entries)

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
    
    msg = f"Hello {patient.name}, 提提您 {appointment.date.strftime('%m月%d日 %H:%M')} 要覆診啊！"
    if appointment.description:
        msg += f" 備註: {appointment.description}"
    
    cal_link = generate_google_calendar_link(
        title=f"覆診Appointment - {patient.name}",
        start_dt=appointment.date,
        description=appointment.description or "Medical Appointment"
    )
    msg += f"\n\nAdd to Google Calendar: {cal_link}"
    
    try:
        client = BaileysClient()
        print(f"Sending manual reminder to {patient.name} ({patient.phone_number})...")
        result = client.send_message(patient.phone_number, msg)
        
        if result:
            appointment.reminded = True
            db.session.commit()
            flash('Reminder sent successfully!', 'success')
        else:
            flash('Failed to send reminder. Check logs.', 'error')
            
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

        # Find patient using hash lookup
        patient = Patient.query.filter_by(phone_hash=hash_data(sender)).first()
        
        if patient:
            client = BaileysClient()
            
            # Check length (max 500 characters)
            if len(message_content) > 500:
                client.send_message(sender, "唔好意思，你輸入嘅內容超過咗500字元。請縮短內容後再發送。如果你有更加多嘢想同醫生講，可以直接喺Whatsapp搵佢！")
                return jsonify({'status': 'ignored', 'reason': 'message_too_long'}), 200
            
            # Check prefix for eDiary
            if not message_content.strip().startswith(('日記：', '日記:')):
                client.send_message(sender, "唔好意思，而家我只係能夠接收你嘅電子日記😔如果你想寫日記俾我哋的話，請喺訊息一開頭包括「日記：」呢個標示！")
                return jsonify({'status': 'ignored', 'reason': 'invalid_format'}), 200

            # Store as Diary Entry
            entry = DiaryEntry(patient_id=patient.id, content=message_content)
            db.session.add(entry)
            db.session.commit()
            
            # Optionally reply confirming receipt
            client.send_message(sender, "感謝！已收到您的電子日記內容。")
            
            return jsonify({'status': 'success', 'message': 'diary_saved'}), 200
        else:
            print(f"Received message from unknown number: {sender}")
            return jsonify({'status': 'ignored', 'reason': 'unknown_patient'}), 200

    except Exception as e:
        print(f"Error processing webhook: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


# --- Application Setup ---

def create_scheduler():
    scheduler = BackgroundScheduler()
    # Check for appointments every hour (or once a day)
    scheduler.add_job(func=scheduler_tasks.send_appointment_reminders, args=[app], trigger="interval", hours=1)
    
    # Send diary reminder every day at 9:00 AM
    scheduler.add_job(func=scheduler_tasks.send_daily_diary_reminders, args=[app], trigger="cron", hour=9, minute=0)
    
    scheduler.start()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    create_scheduler()
    app.run(host='127.0.0.1', debug=False, port=int(os.getenv('PORT', '5000')))
