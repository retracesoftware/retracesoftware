import pytest

from retracesoftware.dap.debug.inspection import Inspector


def test_dataframe_local_gets_structured_expandable_preview():
    pd = pytest.importorskip("pandas")
    inspector = Inspector()
    rates = pd.DataFrame(
        {
            "rate_date": ["2025-01-01", "2025-03-31"],
            "rate": [0.84, 0.87905],
        }
    )

    entry = inspector._var_entry("rates", rates)

    assert entry["type"] == "DataFrame"
    assert entry["value"].startswith("DataFrame shape=(2, 2)")
    assert entry["variablesReference"] > 0

    expanded = inspector.variables(entry["variablesReference"])["variables"]
    names = {item["name"] for item in expanded}
    assert {"shape", "columns", "dtypes", "head", "tail"} <= names
