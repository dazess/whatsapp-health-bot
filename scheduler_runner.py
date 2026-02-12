from app import app, create_scheduler
from models import db


if __name__ == '__main__':
    with app.app_context():
        db.create_all()

    create_scheduler()

    # Keep process alive so scheduler jobs keep running.
    try:
        import time
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass
