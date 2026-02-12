import os
import hashlib
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy.types import TypeDecorator, String, Text
from cryptography.fernet import Fernet

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

class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(EncryptedString(255), nullable=False)
    
    # We store the encrypted phone for display/contact
    phone_encrypted = db.Column(EncryptedString(255), nullable=False)
    # We store a hash for searching (indexes)
    phone_hash = db.Column(db.String(64), unique=True, nullable=False)
    
    appointments = db.relationship('Appointment', backref='patient', lazy=True)
    diary_entries = db.relationship('DiaryEntry', backref='patient', lazy=True)

    @property
    def phone_number(self):
        return self.phone_encrypted

    @phone_number.setter
    def phone_number(self, value):
        self.phone_encrypted = value
        self.phone_hash = hash_data(value)

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, nullable=False)
    description = db.Column(EncryptedString(500), nullable=True) # Encrypted
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    reminded = db.Column(db.Boolean, default=False)

class DiaryEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    content = db.Column(EncryptedText, nullable=False) # Encrypted
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)

