from app import app, db, _migrate_db

with app.app_context():
    db.create_all()
    _migrate_db()

if __name__ == "__main__":
    app.run()
