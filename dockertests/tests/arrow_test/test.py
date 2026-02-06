import arrow


def test_arrow_now():
    now = arrow.now()
    print(f"Now: {now}", flush=True)

    assert isinstance(now, arrow.Arrow), "The 'now' function should return an Arrow object"
    assert now.year == arrow.now().year, "The current year should match"


def test_arrow_formatting():
    date = arrow.get(2023, 10, 2)
    formatted_date = date.format("YYYY-MM-DD")
    print(f"Formatted Date: {formatted_date}", flush=True)

    assert formatted_date == "2023-10-02", "The date should be formatted as 'YYYY-MM-DD'"


def test_arrow_shift():
    date = arrow.get(2023, 10, 2)
    shifted_date = date.shift(days=+10)
    print(f"Shifted Date: {shifted_date}", flush=True)

    assert shifted_date == arrow.get(2023, 10, 12), "The date should be shifted by 10 days"


def test_arrow_humanize():
    past = arrow.get(2022, 1, 1)
    humanized = past.humanize()
    print(f"Humanized: {humanized}", flush=True)

    assert len(humanized) > 0, "The humanized output should not be empty"


if __name__ == "__main__":
    print("=== arrow_test ===", flush=True)
    test_arrow_now()
    test_arrow_formatting()
    test_arrow_shift()
    test_arrow_humanize()
    print("All tests passed successfully!", flush=True)
