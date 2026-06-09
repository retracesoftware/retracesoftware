# Agent Recording Context

Retrace can produce an agent-readable context packet for a local recording.
The packet is evidence-only: it does not call an LLM, infer root cause, or
suggest fixes.

Use it when an agent already has a Retrace recording and needs a stable handoff
with the path, basic file facts, optional manifest metadata, and next commands.

```bash
retrace agent-context --recording ./recording.bin
retrace agent-context --recording ./recording.bin --json
retrace mcp --recording ./recording.bin
```

`--latest` is supported through a generic pointer file:

```text
.retrace/latest-recording.json
```

with at least:

```json
{
  "recording_path": "recordings/failure.bin"
}
```

The recording path may be absolute or relative to the directory containing
`.retrace/`.

Recordings may contain runtime data, secrets, API responses, database-derived
values, or other sensitive application state. Share or upload recordings only
when you intend to.
