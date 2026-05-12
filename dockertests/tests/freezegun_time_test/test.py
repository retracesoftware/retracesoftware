import time
from datetime import date, datetime

from freezegun import freeze_time


@freeze_time("2026-02-14 12:34:56")
def main():
    print("=== freezegun_time_test ===")
    now = datetime.now()
    today = date.today()
    epoch = int(time.time())

    assert now.isoformat() == "2026-02-14T12:34:56"
    assert today.isoformat() == "2026-02-14"
    print(f"now={now.isoformat()}")
    print(f"epoch={epoch}")
    print("freezegun time ok")


if __name__ == "__main__":
    main()
