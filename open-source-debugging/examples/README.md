# Open-source debugging examples

These examples show real open-source Python failures reproduced and inspected
with Retrace.

They are not benchmarks. They do not claim Retrace is necessary to solve every
case. Their purpose is to show concrete third-party failures where replay makes
the failed runtime state visible.

- `001-nbconvert-1731` — Notebook widget metadata bug where the widget-state
  mimetype object existed but lacked the nested `state` key.
- `002-django-modern-rest-1048` — OpenAPI schema generation bug where a query
  parameter referenced `#/components/schemas/TestEnum`, but the enum schema was
  not registered. Manual VS Code replay exposed the caller/failing-frame
  runtime state.
