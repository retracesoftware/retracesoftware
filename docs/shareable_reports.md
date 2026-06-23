# Shareable Retrace Reports

Shareable reports turn an existing Retrace AI report artifact into a public
resolution page or a full diagnostic page.

The input is the JSON artifact written by the AI debugger, for example:

```bash
reports/latest-long.ai-report.json
pytest.ai-report.json
runs/<run-id>/dataframe-long.ai-report.json
```

## Public Preview

Generate a sanitized public report locally:

```bash
retrace report \
  --json reports/latest-long.ai-report.json \
  --repo-root /path/to/project \
  --share
```

If `RETRACE_API_KEY` is not set, upload is skipped and preview files are still
written:

```text
retrace-report.public.json
retrace-report.public.html
```

To also write Markdown:

```bash
retrace report \
  --json reports/latest-long.ai-report.json \
  --repo-root /path/to/project \
  --share \
  --public-markdown reports/share-public.md
```

The public report is sanitized by default. It should not include the raw
`.retrace` recording, full tool transcript, obvious secrets, or absolute local
paths.

## Full Diagnostic Preview

Generate full diagnostic files locally:

```bash
retrace report \
  --json reports/latest-long.ai-report.json \
  --full-json reports/share-full.json \
  --full-html reports/share-full.html \
  --full-markdown reports/share-full.md
```

Full diagnostic reports may include local paths, runtime values, source
snippets, tool calls, and transcript details. Do not share them publicly.

## Local Report Service

Run a local report service:

```bash
retrace report-server \
  --api-key demo-key \
  --host 127.0.0.1 \
  --port 8877 \
  --storage-root /tmp/retrace-report-service
```

Upload a public report to that local service:

```bash
RETRACE_API_KEY=demo-key \
retrace report \
  --json reports/latest-long.ai-report.json \
  --repo-root /path/to/project \
  --share \
  --endpoint http://127.0.0.1:8877/api/reports
```

Upload a full diagnostic report:

```bash
RETRACE_API_KEY=demo-key \
retrace report \
  --json reports/latest-long.ai-report.json \
  --share-full \
  --yes-share-full \
  --endpoint http://127.0.0.1:8877/api/reports
```

Successful uploads print the report URL, delete token, and a delete command:

```text
Public Retrace Resolution Report:
http://127.0.0.1:8877/r/<uuid>
Delete token:
<delete-token>
Delete command:
curl -X DELETE -H 'Authorization: Bearer <delete-token>' http://127.0.0.1:8877/api/reports/<uuid>
```

Keep the delete token. It is the delete credential for the unlisted report.

## Read Endpoints

Public report:

```text
GET /r/<uuid>
GET /r/<uuid>.json
```

Full diagnostic report:

```text
GET /f/<uuid>
GET /f/<uuid>.json
```

Delete:

```bash
curl -X DELETE \
  -H 'Authorization: Bearer <delete-token>' \
  http://127.0.0.1:8877/api/reports/<uuid>
```

Deleted reports return `404`.

## Dataframe Demo Smoke

After running the dataframe demo long path:

```bash
git clone https://github.com/retracesoftware/retrace-dataframe-autodebug-demo.git
cd retrace-dataframe-autodebug-demo
make build
make up
make long
```

Use the generated artifact:

```bash
RETRACE_API_KEY=demo-key \
retrace report \
  --json reports/latest-long.ai-report.json \
  --repo-root dataframe-test-example \
  --share \
  --endpoint http://127.0.0.1:8877/api/reports
```

The public report should include:

- `DataFrame amount_gbp mismatch`;
- replay/DAP evidence such as `DAP stack trace has 43 frame(s)`;
- replay steps in "How Retrace Found This";
- hidden tool transcript.

The full diagnostic report should preserve the tool transcript and diagnostic
paths.

## Automated Local Smoke

From the Retrace repo:

```bash
scripts/smoke_shareable_reports.sh
```

By default this uses `tests/fixtures/dataframe_ai_report.json`, starts a local
report server, uploads public and full reports, verifies read endpoints, and
checks deletion.

Useful overrides:

```bash
REPORT_JSON=/path/to/latest-long.ai-report.json \
REPO_ROOT_FOR_REDACTION=/workspace/dataframe-test-example \
REPORT_PORT=8878 \
scripts/smoke_shareable_reports.sh
```
