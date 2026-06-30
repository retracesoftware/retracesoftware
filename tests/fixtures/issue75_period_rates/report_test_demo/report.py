def _period_rates(currency, period_start, period_end):
    from report_test_demo import db

    rates = db.read_sql("query", None, params=None)
    closing_rate = float(rates[-1]["rate"])
    return {"closing": closing_rate}
