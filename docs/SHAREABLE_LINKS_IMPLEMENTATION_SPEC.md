# Retrace Shareable Links Implementation Spec

## Goal

Retrace Shareable Links turn a successful Retrace diagnosis into a URL that can
be pasted into a GitHub issue, pull request, Slack thread, Discord message, or
social post.

The MVP is primarily a viral open-source bug-fixing feature. A shared report
should make the product promise obvious:

> Retrace found this bug from the failed execution, not from guesswork.

The feature must stay grounded in how Retrace works today:

- a failed Python execution is recorded;
- replay reproduces the failed run deterministically;
- the AI debugger drives replay and debugger tools;
- runtime values, stack frames, source locations, and tool calls are collected;
- a report is generated from replay evidence;
- the report can then be shared.

## Existing Report Input

The current AI debugger path already writes a structured report artifact. In
the Go AI driver this is represented by `report.Artifact`:

```json
{
  "report": {},
  "tool_calls": 12,
  "model_turns": 5,
  "final_session": {},
  "transcript": [],
  "tool_results": []
}
```

The embedded `report` object can contain:

- title;
- status;
- investigation target;
- failure domain or category;
- summary;
- root cause;
- confidence;
- evidence;
- replay walkthrough;
- suggested fix;
- open questions;
- limitations.

The shareable-links implementation should treat this artifact as the initial
source of truth. The full diagnostic report preserves it substantially as-is.
The public report derives a sanitized, brand-forward view from it.

## Product Requirements

Retrace must support two independent shareable report types.

### Public Report

Purpose:

- viral and open-source sharing;
- GitHub issues and pull requests;
- Slack, Discord, and social sharing;
- "I fixed this with Retrace" moments.

URL:

```text
https://retracesoftware.com/r/<uuid>
```

Default command:

```bash
retrace report --share
```

Requirements:

- sanitized by default;
- brand-forward;
- includes root cause, evidence, suggested fix, validation, limitations, and
  "How Retrace found this";
- includes copy-link and copy-GitHub-comment actions;
- includes JSON download;
- includes a "Try Retrace" CTA;
- includes Open Graph metadata for link previews;
- includes noindex/nofollow metadata;
- does not include the raw `.retrace` recording;
- does not include the full unsanitized tool transcript;
- does not include absolute local paths or obvious secrets.

### Full Diagnostic Report

Purpose:

- design partner debugging;
- internal Retrace review;
- trusted collaborator sharing;
- diagnosing how the AI debugger reached its conclusion;
- preserving the current full report output.

URL:

```text
https://retracesoftware.com/f/<uuid>
```

Command:

```bash
retrace report --share-full
```

Requirements:

- complete diagnostic report;
- preserves current report content;
- includes full tool transcript;
- includes model, tool, and final-session metadata;
- includes replay walkthrough;
- may include local paths, source snippets, runtime values, and tool calls;
- has a prominent warning banner;
- includes JSON download;
- includes noindex/nofollow metadata;
- does not upload the raw `.retrace` recording by default.
- requires explicit user confirmation before upload unless
  `--yes-share-full` is supplied for non-interactive use;
- runs a critical secret blocklist before upload and blocks obvious private
  keys or credentials even though the report is otherwise unsanitized.

Public and full reports must use separate UUIDs. A public URL must not reveal or
link to the full report unless the user explicitly requested that behavior in a
future non-MVP option.

## Domain Choice

MVP links use the current public domain:

```text
https://retracesoftware.com/r/<uuid>
https://retracesoftware.com/f/<uuid>
```

Retrace owns `retrace.dev`, but the MVP should reserve it for future developer
docs, open-source distribution, or redirected short links. It should not be the
primary MVP report domain unless the broader domain strategy changes.

## CLI Behavior

### Inputs

The command should run from a project directory and read an AI report artifact.
The safest MVP path is explicit input:

```bash
retrace report --json path/to/ai-report.json --share
retrace report --json path/to/ai-report.json --share-full
retrace report --json path/to/ai-report.json --share --share-full
```

Recommended input resolution:

1. If `--json <path>` is provided and points to an existing AI report artifact,
   use that artifact as input.
2. Otherwise, if the report was produced in the same command or same live
   session, use that report.
3. Otherwise, discovery may look for conventional paths such as
   `*.ai-report.json`, but it must print the exact file before use:

   ```text
   Using AI report artifact:
   ./.retrace/reports/2026-06-23T153000.ai-report.json
   ```

4. If more than one candidate exists, fail and ask the user to pass
   `--json <path>`.
5. If no report can be found, print a clear error explaining how to produce one.

This keeps the MVP compatible with the current report output while leaving room
for `retrace report` to become the canonical report-generation command.

### Commands

Create a public report only:

```bash
retrace report --json path/to/ai-report.json --share
```

Create a full diagnostic report only:

```bash
retrace report --json path/to/ai-report.json --share-full
```

Create both report types:

```bash
retrace report --json path/to/ai-report.json --share --share-full
```

Expected output:

```text
Public Retrace Resolution Report:
https://retracesoftware.com/r/<public_uuid>

Full Retrace Diagnostic Report:
https://retracesoftware.com/f/<full_uuid>
```

Full diagnostic upload must be deliberately explicit. In an interactive
terminal, `--share-full` should print:

```text
Warning: full diagnostic reports may contain sensitive runtime values, local paths, source snippets and tool calls.
Share only with trusted recipients.

Anyone with the link can view the full diagnostic report.

Continue? [y/N]
```

If the user answers anything other than `y` or `yes`, the CLI must skip full
upload. In CI or other non-interactive contexts, the command must fail unless
`--yes-share-full` is present.

### Options

```bash
--repo-root <path>
--redaction standard|strict
--include-tool-transcript
--json <path>
--public-json <path>
--public-html <path>
--public-markdown <path>
--full-json <path>
--full-html <path>
--full-markdown <path>
--yes-share-full
```

Option semantics:

- `--repo-root <path>` is used to convert absolute paths to repo-relative paths.
- `--redaction standard` is the default for public reports.
- `--redaction strict` applies more aggressive value masking and may omit more
  evidence.
- `--redaction none` is not part of the MVP user-facing CLI. Full reports are
  full diagnostic reports by definition and are protected by confirmation plus
  critical secret blocking.
- `--include-tool-transcript` is ignored for public reports in MVP unless a
  future explicit unsafe mode is added.
- `--json <path>` selects the input AI report artifact.
- `--public-json <path>`, `--public-html <path>`, and
  `--public-markdown <path>` choose public preview output paths.
- `--full-json <path>`, `--full-html <path>`, and `--full-markdown <path>`
  choose full diagnostic output paths.
- `--yes-share-full` is required for non-interactive full diagnostic upload.

Defaults:

```text
--share creates public report
redaction = standard
include tool transcript in public report = false
include raw trace = false
visibility = unlisted
public preview JSON = retrace-report.public.json
public preview HTML = retrace-report.public.html
```

Public preview files are generated before upload even when the user only asks
for `--share`. The CLI should print their paths and remind the user to review
the hosted report before posting it publicly.

### Authentication

Local JSON, Markdown, and HTML report generation must work without
authentication.

Uploading a share link requires a Retrace API key:

```bash
export RETRACE_API_KEY=...
retrace report --json path/to/ai-report.json --share
```

MVP should not require browser login for viewing report links.

If no API key is configured for an upload command, the CLI should:

1. generate local artifacts if requested;
2. skip upload;
3. print a short setup message for `RETRACE_API_KEY`.

For design partners, an API key is acceptable for the MVP. For a later
open-source launch, consider `retrace auth login` or a limited unauthenticated
upload path with strict size and rate limits. Do not block the MVP on that
future path.

## Report Data Model

The hosted page renders from canonical JSON. HTML and Markdown are rendered
views of that JSON.

### Source Artifact

The source artifact is the current AI debugger output:

```json
{
  "report": {
    "title": "Bad branch",
    "status": "diagnosed",
    "summary": "...",
    "root_cause": {},
    "evidence": [],
    "replay_walkthrough": [],
    "suggested_fix": {},
    "open_questions": [],
    "limitations": []
  },
  "tool_calls": 12,
  "model_turns": 5,
  "final_session": {},
  "transcript": [],
  "tool_results": []
}
```

### Public Report JSON

Minimum public shape:

```json
{
  "schema_version": "retrace.resolution_report.v1",
  "report_id": "uuid",
  "mode": "public",
  "created_at": "ISO-8601 timestamp",
  "title": "...",
  "status": "diagnosed",
  "confidence": {
    "level": "medium",
    "reason": "..."
  },
  "failure": {
    "category": "...",
    "test": "...",
    "location": {
      "path": "...",
      "line": 123
    }
  },
  "root_cause": {
    "summary": "...",
    "location": {
      "path": "...",
      "line": 123,
      "function": "..."
    }
  },
  "summary": "...",
  "evidence": [],
  "suggested_fix": {
    "summary": "...",
    "files": [],
    "test": "..."
  },
  "validation": {
    "commands": []
  },
  "limitations": [],
  "how_retrace_found_this": [],
  "privacy": {
    "sanitized": true,
    "trace_shared": false,
    "runtime_values_included": true,
    "tool_transcript_included": false
  }
}
```

### Full Diagnostic JSON

Minimum full shape:

```json
{
  "schema_version": "retrace.resolution_report.v1",
  "report_id": "uuid",
  "mode": "full",
  "created_at": "ISO-8601 timestamp",
  "title": "...",
  "status": "diagnosed",
  "summary": "...",
  "root_cause": {},
  "evidence": [],
  "replay_walkthrough": [],
  "suggested_fix": {},
  "open_questions": [],
  "limitations": [],
  "tool_transcript": [],
  "raw_metadata": {
    "tool_calls": 12,
    "model_turns": 5,
    "final_session": {}
  },
  "privacy": {
    "sanitized": false,
    "trace_shared": false,
    "runtime_values_included": true,
    "tool_transcript_included": true
  }
}
```

### Evidence Item Shape

Public evidence items should be concise and grounded in runtime observations:

```json
{
  "claim": "The failing discount branch was selected at runtime.",
  "tool": "get_stack_trace",
  "location": {
    "path": "src/pricing.py",
    "line": 42
  },
  "observed": "discount_basis='percent', discount_amount=30"
}
```

Evidence may mention variables, selected branches, query strings, source lines,
stack frames, replay stop locations, and inspected runtime locals. It must not
dump the full transcript.

## Generation Flow

### Public Report Flow

```text
AI debugger artifact
  -> parse source artifact
  -> derive full diagnostic model
  -> derive public model
  -> sanitize public model
  -> render local JSON/Markdown/HTML
  -> POST public payload to report service
  -> receive https://retracesoftware.com/r/<uuid>
```

Public generation must fail closed. If standard redaction cannot process a
field safely, the field should be omitted or replaced with `[redacted]`.

### Full Diagnostic Flow

```text
AI debugger artifact
  -> parse source artifact
  -> derive full diagnostic model
  -> render local JSON/Markdown/HTML
  -> POST full payload to report service
  -> receive https://retracesoftware.com/f/<uuid>
```

Full diagnostic generation preserves the transcript and raw metadata. It should
still exclude the raw `.retrace` recording by default.

### Creating Both

For:

```bash
retrace report --share --share-full
```

the CLI should create two independent payloads and upload them separately. The
server generates two separate UUIDs. The records may store a nullable
`paired_report_id` for internal support, but the public page must not reveal the
full report URL.

## Sanitization and Redaction

Public reports must pass through sanitization before upload.

Minimum rules:

1. Convert absolute paths to repo-relative paths when `--repo-root` is known.
2. If a path cannot be made relative, replace home directories and temp roots,
   for example `/Users/alice/project/src/app.py` becomes `src/app.py` or
   `[repo]/src/app.py`.
3. Mask obvious secrets:
   - API keys;
   - bearer tokens;
   - passwords;
   - private keys;
   - database URLs;
   - auth headers;
   - cloud credential fields.
4. Truncate long runtime values.
5. Limit large object, dataframe, list, dict, and binary previews.
6. Hide the full tool transcript.
7. Remove raw session IDs unless they are useful and safe.
8. Exclude raw `.retrace` recordings.
9. Include privacy metadata in JSON.

Suggested masks:

```text
sk-... -> [redacted_api_key]
Authorization: Bearer ... -> Authorization: Bearer [redacted]
postgres://user:pass@host/db -> postgres://[redacted]
-----BEGIN PRIVATE KEY----- ... -> [redacted_private_key]
```

Suggested truncation defaults:

- individual string value: 500 characters in standard mode;
- individual evidence observed field: 1,000 characters;
- dataframe/list preview: 20 rows or entries;
- full public report JSON: reject or require strict mode above a server-defined
  size limit.

Strict mode:

- shorter value previews;
- removes source snippets longer than one line;
- removes query literals when they look like user or customer data;
- masks all environment variable values.

Full mode:

- no broad sanitization by default;
- still marks `trace_shared=false`;
- shows a warning banner before content;
- runs a critical secret blocklist and blocks upload if an obvious private key,
  API token, credential URL, or auth header is detected.

If a full report hits the critical secret blocklist, the CLI should print:

```text
Full report upload blocked: possible private key or credential detected.
Full diagnostic sharing is disabled for this report.
```

Do not add an override such as `--allow-sensitive-upload` in the MVP unless it
is absolutely required for a design partner.

## Safe Rendering

Report values are untrusted input. Runtime values, source snippets, SQL,
tracebacks, tool results, transcript content, file paths, and model text must be
HTML-escaped by default.

Rendering rules:

- never inject report values as raw HTML;
- escape all string fields before placing them in HTML;
- render code and Markdown through a safe renderer with raw HTML disabled;
- escape Open Graph values;
- use a strict content security policy where possible;
- add golden tests with malicious values such as `<script>alert(1)</script>`,
  `"><img src=x onerror=alert(1)>`, and Markdown containing inline HTML.

The hosted service may store pre-rendered HTML from the CLI, but it must treat
that HTML as a generated artifact from trusted Retrace code, not as arbitrary
user-authored HTML. If the service ever accepts third-party HTML directly, it
must sanitize or reject it.

## Link Creation Flow

Recommended MVP architecture:

```text
retrace CLI
  -> generate full diagnostic report from current AI debugger output
  -> generate sanitized public report from full report
  -> render public JSON/HTML preview locally
  -> render optional Markdown locally
  -> POST report payload to report service
  -> service creates UUID
  -> service stores report
  -> service returns URL
```

Server must generate UUIDs. The client must not choose report IDs.

Rationale for CLI-side rendering:

- local report equals hosted report;
- backend stays simple;
- users can use reports without hosting;
- faster to ship.

Backend responsibilities:

- create UUID;
- store artifacts;
- serve HTML and JSON;
- support deletion and takedown.

Analytics, polished client-side copy buttons, and future account/workspace
features should follow after the minimal upload/read/delete path works.

### MVP of MVP

The first hosted implementation should contain only:

1. `POST /api/reports`;
2. storage for public/full HTML plus canonical JSON;
3. `/r/<uuid>` and `/f/<uuid>` page reads;
4. `/r/<uuid>.json` and `/f/<uuid>.json` JSON reads;
5. noindex/nofollow;
6. maximum report size;
7. API key upload authentication;
8. delete-token based deletion.

## Backend API

### Create Report

```http
POST /api/reports
Authorization: Bearer <RETRACE_API_KEY>
Content-Type: application/json
```

Public request:

```json
{
  "mode": "public",
  "schema_version": "retrace.resolution_report.v1",
  "report": {},
  "html": "<html>...</html>",
  "markdown": "...",
  "visibility": "unlisted",
  "redaction_mode": "standard"
}
```

Public response:

```json
{
  "report_id": "uuid",
  "url": "https://retracesoftware.com/r/<uuid>",
  "delete_token": "opaque-delete-token"
}
```

Full request:

```json
{
  "mode": "full",
  "schema_version": "retrace.resolution_report.v1",
  "report": {},
  "html": "<html>...</html>",
  "markdown": "...",
  "visibility": "unlisted",
  "redaction_mode": "full_diagnostic_with_critical_secret_blocklist"
}
```

Full response:

```json
{
  "report_id": "uuid",
  "url": "https://retracesoftware.com/f/<uuid>",
  "delete_token": "opaque-delete-token"
}
```

Validation:

- `mode` must be `public` or `full`.
- `schema_version` must be accepted by the server.
- `visibility` must be `unlisted` for MVP.
- `html`, `markdown`, and `report` must satisfy size limits.
- public reports must declare `privacy.sanitized=true`.
- public reports must declare `privacy.tool_transcript_included=false`.
- full reports must declare `privacy.trace_shared=false` unless a future raw
  trace upload feature is explicitly added.
- full reports must pass the critical secret blocklist before storage.
- public and full HTML must be generated with escaped report values.

### Read Pages

```http
GET /r/<uuid>
GET /f/<uuid>
```

Returns stored HTML.

### JSON Endpoints

```http
GET /r/<uuid>.json
GET /f/<uuid>.json
```

Returns canonical JSON with:

```http
Content-Type: application/json
Cache-Control: public, max-age=300
```

### Delete

MVP can support delete-token based deletion without accounts:

```http
DELETE /api/reports/<uuid>
Authorization: Bearer <delete_token>
```

Deletion should set `deleted_at` and make public reads return 404 or a deleted
placeholder.

## Storage Model

Keep storage simple for MVP.

### Option A: Database Blobs

Store:

- id;
- mode;
- schema_version;
- json;
- html;
- markdown;
- created_at;
- visibility;
- redaction_mode;
- paired_report_id nullable;
- delete_token_hash nullable;
- deleted_at nullable;
- uploader identity or API key id nullable.

This is fastest if the current hosting stack already has a relational database.

### Option B: DB Metadata + Object Storage

Store metadata in DB and report files in object storage.

Object keys:

```text
reports/public/<uuid>/report.json
reports/public/<uuid>/report.html
reports/public/<uuid>/report.md
reports/full/<uuid>/report.json
reports/full/<uuid>/report.html
reports/full/<uuid>/report.md
```

Prefer whichever option is fastest with current infrastructure. The service
contract should not expose which option is used.

## Public Frontend Page

Public report pages should be polished, short, and credible.

URL:

```text
/r/<uuid>
```

Page structure:

1. Header.
2. Viral proof line.
3. What happened.
4. Root cause.
5. Evidence.
6. Suggested fix.
7. Validation.
8. Limitations.
9. How Retrace found this.
10. Privacy badge.
11. CTAs.

### Header

Fields:

- `Retrace Resolution Report`;
- title;
- status;
- confidence;
- failure category;
- generated timestamp;
- repo or project if available.

### Viral Proof Line

Use copy close to:

```text
Retrace replayed the failed execution and found the runtime evidence behind this bug.
```

### Evidence

Show 3 to 6 concise evidence cards. Each card should include:

- evidence claim;
- source location if available;
- observed runtime fact;
- optional tool name in subdued metadata.

### How Retrace Found This

This section is required because it explains the mechanism and supports the
marketing message.

Example:

```text
1. Started deterministic replay of the failed execution.
2. Set a breakpoint in the relevant code path.
3. Stopped inside the function where the failing value was computed.
4. Inspected runtime locals.
5. Stepped over the assignment.
6. Observed the runtime value.
7. Inspected the query/source.
8. Linked the observed value to the root cause.
```

The section must be sanitized and concise.

### Privacy Badge

Example:

```text
Privacy: Sanitized
Trace shared: No
Runtime values: Included, redacted
Tool transcript: Hidden
```

### CTAs

- Copy link.
- Copy GitHub comment.
- View/download JSON.
- Try Retrace on your Python tests.

The CTA should be restrained. The report should first serve the maintainer or
reviewer reading the bug diagnosis; marketing is the by-product.

Good copy:

```text
Generated by Retrace - replay-backed debugging for Python.
Try Retrace on your Python tests.
```

Avoid hype-heavy copy such as:

```text
Stop debugging the old way! Sign up now!
```

### Copy GitHub Comment

Generate Markdown like:

```markdown
Retrace replayed the failed execution and found the runtime evidence behind this bug.

Root cause: <one sentence>

Evidence:
- <evidence 1>
- <evidence 2>
- <evidence 3>

Report: https://retracesoftware.com/r/<uuid>
```

## Full Diagnostic Frontend Page

URL:

```text
/f/<uuid>
```

Page structure:

1. Warning banner.
2. Report title.
3. Metadata table.
4. Summary.
5. Root cause.
6. Evidence.
7. Replay walkthrough.
8. Suggested fix.
9. Open questions.
10. Limitations.
11. Full tool transcript.
12. JSON download.

Warning banner:

```text
Full diagnostic report

This report may contain local paths, runtime values, source snippets, tool calls, and other sensitive debugging information. Share only with trusted recipients.
```

Security copy:

```text
Anyone with this link can view this full diagnostic report. It may contain sensitive information.
```

## JSON Endpoints

Both report modes must expose JSON:

```text
https://retracesoftware.com/r/<uuid>.json
https://retracesoftware.com/f/<uuid>.json
```

JSON endpoints are useful for:

- GitHub issue bots or future GitHub app integration;
- design partner handoff;
- regression testing hosted rendering;
- downloading evidence for local review.

The JSON must be the canonical report model, not an HTML-derived scrape.

## Open Graph and Sharing

Public reports should render well when pasted into GitHub, Slack, Discord,
LinkedIn, X, and similar surfaces.

Public pages should include:

```html
<meta property="og:title" content="Retrace found root cause: <title>" />
<meta property="og:description" content="Generated from replayed runtime evidence, not guesswork." />
<meta property="og:type" content="article" />
<meta property="og:url" content="https://retracesoftware.com/r/<uuid>" />
<meta name="robots" content="noindex,nofollow" />
```

Full reports should also include:

```html
<meta name="robots" content="noindex,nofollow" />
```

The MVP should not rely on search indexing for discovery.

## Analytics Events

Public reports are intended for viral marketing, so basic analytics are needed
for the shareable-links launch. They should not block the MVP-of-MVP upload and
read path, but they should land before a broader open-source launch. Do not
track sensitive report content.

Public events:

- `report_created`;
- `report_viewed`;
- `copy_link_clicked`;
- `copy_github_comment_clicked`;
- `json_downloaded`;
- `try_retrace_clicked`.

Full events:

- `full_report_created`;
- `full_report_viewed`;
- `full_json_downloaded`.

Fields:

- report_id;
- mode;
- action;
- timestamp;
- referrer;
- user agent;
- UTM source if present.

Do not log:

- report body;
- runtime values;
- source snippets;
- tool transcript;
- raw trace content.

## Abuse and Safety Controls

Minimum MVP controls:

- maximum report size;
- API-key rate limits for upload;
- per-IP rate limits for unauthenticated page views if needed;
- reject public reports with obvious private keys if strict redaction fails;
- reject full reports with obvious private keys or credentials before upload;
- delete or takedown support;
- noindex/nofollow;
- full report warning banner;
- interactive confirmation for `--share-full`;
- clear unlisted-link wording;
- no raw `.retrace` upload by default.

Security wording for public pages:

```text
Anyone with this link can view this public report.
```

Security wording for full pages:

```text
Anyone with this link can view this full diagnostic report. It may contain sensitive information.
```

Do not claim UUID links are secure or private. They are unlisted, not access
controlled.

## Non-Goals

The MVP does not include:

- accounts;
- workspaces;
- permissions;
- comments;
- full replay viewer;
- source browser;
- GitHub app;
- automatic PR creation;
- enterprise access controls;
- report dashboard;
- raw trace sharing.

## Acceptance Criteria

1. `retrace report --share` creates a public shareable URL.
2. `retrace report --share-full` creates a full diagnostic URL.
3. `retrace report --share --share-full` creates both.
4. Public and full reports use separate UUIDs.
5. Public report page is sanitized and branded.
6. Public report includes root cause, evidence, suggested fix, validation,
   limitations, and "How Retrace found this".
7. Public report has copy link, copy GitHub comment, JSON download, and Try
   Retrace CTA.
8. Full report preserves the current detailed report content.
9. Full report has a warning banner.
10. Raw `.retrace` files are not uploaded by default.
11. Public report removes absolute paths and obvious secrets.
12. `.json` endpoints exist for both modes.
13. Basic analytics are captured without report content.
14. Public links look good in Slack, GitHub, Discord, and social previews.
15. Links are labelled as unlisted, not secure or private.
16. Upload requires `RETRACE_API_KEY`; local rendering does not.
17. `--share-full` requires interactive confirmation or `--yes-share-full`.
18. Public preview files are generated before upload.
19. All runtime values, source snippets, SQL, tool results, and transcript
    content are HTML-escaped by default.
20. Full diagnostic upload is blocked when the critical secret blocklist finds
    an obvious private key or credential.
21. If multiple discovered report candidates exist, the CLI fails and asks for
    `--json <path>`.

## Suggested Implementation Phases

### Phase 1: Report Model and Renderers

- Identify the current AI debugger artifact shape.
- Define `FullDiagnosticReport` and `PublicResolutionReport` models.
- Add adapter from current report artifact to full diagnostic model.
- Add adapter from full diagnostic model to public model.
- Implement public sanitization.
- Implement JSON, Markdown, and HTML renderers.
- Escape all report values in HTML renderers by default.
- Add critical secret blocklist scanning for both public and full upload.
- Add golden-file tests for both modes.

### Phase 2: CLI Share Commands

- Add `retrace report --share`.
- Add `retrace report --share-full`.
- Add `retrace report --share --share-full`.
- Add `--json <path>` for explicit input report selection.
- Add local public/full JSON, Markdown, and HTML preview output options.
- Add `--repo-root`.
- Add `--redaction standard|strict` for public reports.
- Add public preview files generated before upload.
- Add `--yes-share-full` and interactive full-share confirmation.
- Add `RETRACE_API_KEY` configuration.
- Print clear report URLs and local artifact paths.

### Phase 3: Report Service MVP

- Implement `POST /api/reports`.
- Generate server-side UUIDs.
- Store JSON, HTML, and Markdown.
- Serve `/r/<uuid>`, `/r/<uuid>.json`, `/f/<uuid>`, and `/f/<uuid>.json`.
- Add delete-token based deletion or an equivalent takedown path.
- Defer analytics and polished copy-button behavior until this core path works.

### Phase 4: Public Page Polish

- Add branded public template.
- Add CTAs.
- Add copy GitHub comment.
- Add Open Graph metadata.
- Add privacy badge.
- Add "How Retrace found this".
- Keep marketing copy restrained and maintainer-friendly.
- Verify previews in GitHub, Slack, Discord, LinkedIn, and X.

### Phase 5: Analytics and Safety

- Add view and click events.
- Add rate limiting.
- Add max report size.
- Add noindex/nofollow.
- Add upload rejection for high-confidence leaked secrets.
- Add takedown/delete support.

## Implementation Notes

- Keep the public report derived from the full report so that public and full
  outputs do not diverge semantically.
- Keep the raw `.retrace` file local by default. Sharing raw traces can be a
  future explicit feature with stronger warnings and access control.
- Keep server storage opaque to the CLI. The CLI should care only about
  `POST /api/reports` and the returned URL.
- Keep public report evidence concise. The best public artifact is credible
  because it shows a few runtime facts, not because it dumps every tool call.
- Keep limitations visible. Honest limitations make the report more trustworthy.
- Prefer additive schema evolution. Include `schema_version` in all payloads and
  make readers ignore unknown fields.
