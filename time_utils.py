from datetime import datetime, timedelta, timezone


GMT_PLUS_8 = timezone(timedelta(hours=8))


def now_gmt8():
    return datetime.now(GMT_PLUS_8)


def today_gmt8():
    return now_gmt8().date()


def now_gmt8_naive():
    """Return local GMT+8 time without tzinfo for SQLite DateTime columns."""
    return now_gmt8().replace(tzinfo=None)
