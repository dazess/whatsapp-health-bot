import os
import hashlib
import uuid
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.types import TypeDecorator, String, Text
from sqlalchemy import UniqueConstraint
from cryptography.fernet import Fernet
from time_utils import now_gmt8_naive

db = SQLAlchemy()

def get_encryption_key():
    key = os.getenv('ENCRYPTION_KEY')
    if not key:
        # Fallback for when .env isn't loaded yet during import, 
        # but runtime it should be fine.
        # Returning a dummy key here would be dangerous.
        # We rely on os.getenv working at runtime.
        return None 
    return key

class EncryptedType(TypeDecorator):
    """Abstract generic EncryptedType"""
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        key = get_encryption_key()
        if value is None or not key:
            return value
        f = Fernet(key)
        if isinstance(value, str):
            value = value.encode('utf-8')
        return f.encrypt(value).decode('utf-8')

    def process_result_value(self, value, dialect):
        key = get_encryption_key()
        if value is None or not key:
            return value
        f = Fernet(key)
        try:
            return f.decrypt(value.encode('utf-8')).decode('utf-8')
        except Exception:
            # In case of decryption failure (e.g. old plain data), return as is or error
            return value

class EncryptedString(EncryptedType):
    impl = String

class EncryptedText(EncryptedType):
    impl = Text

def hash_data(data):
    """Deterministic hash for lookups"""
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def generate_unique_phone_hash(phone_number, pid=None):
    """Generates a unique hash so duplicate phone numbers can coexist."""
    suffix = pid if pid else uuid.uuid4().hex
    return hash_data(f"{phone_number}|{suffix}")

class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pid = db.Column(db.String(20), unique=True, nullable=True)
    name = db.Column(EncryptedString(255), nullable=False)

    # Encrypted phone for display/contact
    phone_encrypted = db.Column(EncryptedString(255), nullable=False)
    # Legacy unique hash kept to avoid SQLite table rebuild for existing installs
    phone_hash = db.Column(db.String(64), unique=True, nullable=False)
    # Non-unique lookup hash supports shared phone numbers
    phone_lookup_hash = db.Column(db.String(64), nullable=True, index=True)

    # Birthday (plain date – no PII beyond what name already reveals)
    birthdate = db.Column(db.Date, nullable=True)

    # Free-text description used by AI to personalise the birthday card
    description = db.Column(EncryptedText, nullable=True)

    # Track whether a birthday card was already sent this calendar year
    birthday_card_sent_year = db.Column(db.Integer, nullable=True)

    # Whether to send daily Qualtrics survey reminders
    send_survey_reminders = db.Column(db.Boolean, default=True, nullable=False)

    # Date of latest survey reminder sent (for per-day dedupe)
    last_survey_reminder_date = db.Column(db.Date, nullable=True)

    # Track if the patient has received an initial greeting
    greeted = db.Column(db.Boolean, default=False, nullable=False)

    appointments = db.relationship('Appointment', backref='patient', lazy=True)

    @property
    def phone_number(self):
        return self.phone_encrypted

    @phone_number.setter
    def phone_number(self, value):
        self.phone_encrypted = value
        self.phone_lookup_hash = hash_data(value)
        self.phone_hash = generate_unique_phone_hash(value, self.pid)

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, nullable=False)
    description = db.Column(EncryptedString(500), nullable=True) # Encrypted
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    reminded = db.Column(db.Boolean, default=False)


class QualtricsResponse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    survey_code = db.Column(db.String(100), nullable=True, index=True)
    pid = db.Column(db.String(20), nullable=False, index=True)
    qualtrics_response_id = db.Column(db.String(100), unique=True, nullable=False)
    recorded_at = db.Column(db.DateTime, nullable=True)
    last_seen_at = db.Column(db.DateTime, default=now_gmt8_naive, nullable=False)


class SurveyLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    url = db.Column(db.String(1024), nullable=False)
    qualtrics_survey_id = db.Column(db.String(100), nullable=True)
    pid_field = db.Column(db.String(50), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=now_gmt8_naive, nullable=False)


class SurveyReminderLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    survey_link_id = db.Column(db.Integer, db.ForeignKey('survey_link.id'), nullable=False)
    sent_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=now_gmt8_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint('patient_id', 'survey_link_id', 'sent_date', name='uq_survey_reminder_daily'),
    )


class SurveyReminderEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False, index=True)
    survey_code = db.Column(db.String(200), nullable=False, index=True)
    sent_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=now_gmt8_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint('patient_id', 'survey_code', 'sent_date', name='uq_survey_event_daily'),
    )


class SurveyReminderEscalation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False, index=True)
    survey_code = db.Column(db.String(200), nullable=False, index=True)
    reminder_count = db.Column(db.Integer, nullable=False)
    recipients = db.Column(db.Text, nullable=True)
    alerted_at = db.Column(db.DateTime, default=now_gmt8_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint('patient_id', 'survey_code', name='uq_survey_escalation_once'),
    )


class SurveyLinkOverride(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    survey_code = db.Column(db.String(200), unique=True, nullable=False, index=True)
    survey_link = db.Column(db.String(1024), nullable=False)
    updated_at = db.Column(db.DateTime, default=now_gmt8_naive, onupdate=now_gmt8_naive, nullable=False)


class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    setting_value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=now_gmt8_naive, onupdate=now_gmt8_naive, nullable=False)

