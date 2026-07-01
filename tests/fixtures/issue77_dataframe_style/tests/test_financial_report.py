def test_generate_financial_report():
    expected = 59463.2
    actual = 63750.41
    raise AssertionError(
        'DataFrame.iloc[:, 3] (column name="amount_gbp") are different\n'
        f"At positional index 249, first diff: {actual} != {expected}"
    )
