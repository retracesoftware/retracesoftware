# Handle missing widget state metadata in WidgetsDataTypeFilter

This fixes a `KeyError` when notebook metadata contains the widget-state
mimetype object but that object does not include a nested `"state"` key.

Reproducer shape:

```python
metadata = {
    "widgets": {
        "application/vnd.jupyter.widget-state+json": {
            "version_major": 2,
            "version_minor": 0,
        }
    }
}
```

When an output includes `application/vnd.jupyter.widget-view+json`,
`WidgetsDataTypeFilter` currently indexes:

```python
metadata["widgets"][WIDGET_STATE_MIMETYPE]["state"]
```

and raises:

```text
KeyError: "state"
```

This change treats missing `"state"` the same as unavailable widget state. The
filter skips the widget-view mimetype and falls back to the next available
display format, such as `text/plain`.

Tests added:

* widget metadata without `"state"` falls back to `text/plain`
* widget metadata with matching state still prefers widget-view

Validation:

```text
tests/filters/test_widgetsdatatypefilter.py tests/filters/test_datatypefilter.py:
5 passed

tests/filters:
54 passed, 5 skipped

full test suite:
290 passed, 41 skipped
```

Related issues:

* #1731
* #2127
