from datetime import datetime

import pytz
from dateutil import parser
from dateutil.relativedelta import relativedelta


def test_dateutil_parsing():
    date_str = "2024-09-25 12:45:30"
    parsed_date = parser.parse(date_str)

    expected_date = datetime(2024, 9, 25, 12, 45, 30)
    assert parsed_date == expected_date, f"Parsed date does not match expected: {parsed_date} != {expected_date}"

    print("Test passed! Date parsing works correctly.", flush=True)


def test_dateutil_relativedelta():
    initial_date = datetime(2024, 9, 25)
    new_date = initial_date + relativedelta(months=+3)

    expected_new_date = datetime(2024, 12, 25)
    assert new_date == expected_new_date, f"Date manipulation failed: {new_date} != {expected_new_date}"

    print("Test passed! Relative delta manipulation works correctly.", flush=True)


def test_dateutil_timezone_handling():
    utc_date = datetime(2024, 9, 25, 12, 45, 30, tzinfo=pytz.UTC)
    est = pytz.timezone("US/Eastern")
    est_date = utc_date.astimezone(est)

    # September is typically EDT (UTC-4)
    expected_est_date = datetime(2024, 9, 25, 8, 45, 30, tzinfo=est)

    assert est_date.replace(tzinfo=None) == expected_est_date.replace(
        tzinfo=None
    ), f"Timezone conversion failed: {est_date} != {expected_est_date}"

    print("Test passed! Timezone conversion works correctly.", flush=True)


if __name__ == "__main__":
    print("=== dateutil_test ===", flush=True)
    test_dateutil_parsing()
    test_dateutil_relativedelta()
    test_dateutil_timezone_handling()
