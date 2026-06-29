import sys

import pytest

from retracesoftware.control_runtime import FrameInspector, _find_user_frame


def test_find_user_frame_keeps_user_path_containing_retracesoftware():
    filename = "/tmp/user-retracesoftware-project/app.py"
    namespace = {"_find_user_frame": _find_user_frame}
    exec(
        compile(
            "def target():\n"
            "    return _find_user_frame()\n",
            filename,
            "exec",
        ),
        namespace,
    )

    frame = namespace["target"]()

    assert frame is not None
    assert frame.f_code.co_filename == filename


def test_frame_inspector_gives_dataframe_locals_structured_preview():
    pd = pytest.importorskip("pandas")

    def capture_variables():
        rates = pd.DataFrame(
            {
                "rate_date": ["2025-01-01", "2025-03-31"],
                "rate": [0.84, 0.87905],
            }
        )
        return FrameInspector(sys._getframe()).locals({"repr_budget": 1200})["variables"]

    variables = capture_variables()
    rates_var = next(variable for variable in variables if variable["name"] == "rates")

    assert rates_var["type"] == "DataFrame"
    assert rates_var["value"].startswith("DataFrame shape=(2, 2)")
    child_names = {child["name"] for child in rates_var["children"]}
    assert {"shape", "columns", "dtypes", "head", "tail"} <= child_names
