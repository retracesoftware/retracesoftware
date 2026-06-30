import report_test_demo.report as report


def test_period_rates_uses_latest_date_for_closing_rate(monkeypatch):
    returned_without_order_by = [
        {"rate": 0.819934},
        {"rate": 0.820536},
        {"rate": 0.878772},
        {"rate": 0.879050},
    ]

    def read_sql(query, database, params=None):
        return returned_without_order_by

    monkeypatch.setattr("report_test_demo.db.read_sql", read_sql)

    rates = report._period_rates("EUR", "2025-01-01", "2025-03-31")

    assert rates["closing"] == 0.819934
