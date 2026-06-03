"""
Reduced reproducer for jupyter/nbconvert#1731.

The metadata contains the widget-state mimetype object but lacks the nested
"state" key. nbconvert indexes that key unconditionally and raises KeyError:
"state".
"""

from __future__ import annotations

import json
from pathlib import Path

from nbconvert.filters.widgetsdatatypefilter import WIDGET_VIEW_MIMETYPE, WidgetsDataTypeFilter


def load_colab_metadata() -> dict:
    payload_path = Path(__file__).with_name("colab_widget_metadata.json")
    return json.loads(payload_path.read_text())


def choose_output_format(metadata: dict) -> list[str]:
    output = {
        WIDGET_VIEW_MIMETYPE: {
            "model_id": "missing-widget-model",
        },
        "text/plain": "widget fallback",
    }
    filter_ = WidgetsDataTypeFilter(notebook_metadata={"": metadata})
    return filter_(output)


def main() -> None:
    metadata = load_colab_metadata()
    formats = choose_output_format(metadata)
    assert formats == ["text/plain"]


if __name__ == "__main__":
    main()
