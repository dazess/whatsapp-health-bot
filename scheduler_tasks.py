from datetime import datetime, timedelta
from models import db, Patient, Appointment
from services import BaileysClient, generate_google_calendar_link

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
            patient = appointment.patient
            msg = f"Hello {patient.name}, this is a reminder for your appointment tomorrow at {appointment.date.strftime('%H:%M')}."
            
            cal_link = generate_google_calendar_link(
                title=f"Medical Appointment - {patient.name}",
                start_dt=appointment.date,
                description=appointment.description or "Medical Appointment"
            )
            msg += f"\n\nAdd to Google Calendar: {cal_link}"
            
            print(f"Sending reminder to {patient.name} ({patient.phone_number})...")
            result = client.send_message(patient.phone_number, msg)
            
            if result:
                appointment.reminded = True
                db.session.commit()

def send_daily_diary_reminders(app):
    """
    Sends a daily reminder to all patients to fill out their e-diary.
    """
    with app.app_context():
        patients = Patient.query.all()
        client = BaileysClient()
        
        msg = "Good morning! Please remember to send your daily e-diary entry. Just reply with your entry."
        
        for patient in patients:
            print(f"Sending diary reminder to {patient.name}...")
            client.send_message(patient.phone_number, msg)
