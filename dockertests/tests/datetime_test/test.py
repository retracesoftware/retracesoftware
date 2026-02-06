import datetime
import time


def test_datetime_with_utc():
    tz = datetime.timezone.utc
    now = datetime.datetime.now(tz)
    print(f"Now with UTC timezone: {now}", flush=True)
    assert isinstance(now, datetime.datetime)


def test_datetime_with_local_tz():
    local_tz = datetime.timezone(datetime.timedelta(seconds=-time.timezone))
    now = datetime.datetime.now(local_tz)
    print(f"Now with local timezone: {now}", flush=True)
    assert now.tzinfo is not None


def test_datetime_comparison():
    dt1 = datetime.datetime(2023, 10, 2, tzinfo=datetime.timezone.utc)
    dt2 = dt1 + datetime.timedelta(days=10)
    print(f"Shifted datetime: {dt2}", flush=True)
    assert (dt2 - dt1).days == 10


if __name__ == "__main__":
    print("=== datetime_test ===", flush=True)
    test_datetime_with_utc()
    test_datetime_with_local_tz()
    test_datetime_comparison()
    print("All datetime tests passed.", flush=True)
