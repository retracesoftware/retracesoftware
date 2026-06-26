# Agent Recording Context

Retrace can produce an agent-readable context packet for a local recording.
The packet is evidence-only: it does not call an LLM, infer root cause, or
suggest fixes.

Use it when an agent already has a Retrace recording and needs a stable handoff
with the path, basic file facts, optional manifest metadata, and next commands.

```bash
retrace agent-context --recording ./recording.bin
retrace agent-context --recording ./recording.bin --json
retrace diagnose --recording ./recording.bin
retrace diagnose --recording ./recording.bin --json
retrace failures --recording ./recording.bin
retrace failures --recording ./recording.bin --json
retrace function-code --recording ./recording.bin --frame 0
retrace function-code --recording ./recording.bin --frame 0 --json
retrace eval --recording ./recording.bin --frame 0 --expression 'total'
retrace eval --recording ./recording.bin --frame 0 --expression 'items[0].price' --json
retrace mcp --recording ./recording.bin
```

`retrace diagnose` closes the first agent loop without calling an LLM. It runs
the inspect backend once, summarizes the observed failure evidence, ranks
hypotheses, and emits specific next MCP tool calls such as `retrace_frame`,
`retrace_failures`, `retrace_function_code`, `retrace_eval`, `retrace_var`,
`retrace_provenance`, and `retrace_external_calls`. The output is still
evidence-first: hypotheses are prompts for the next inspection step, not
root-cause claims.

`retrace failures` and the MCP tool `retrace_failures` search replay for raised
exception candidates. Results include cursors, exception summaries, locations,
and classifications such as application, stdlib, site-packages, or Retrace
internal. This is the raw primitive for UIs and agents that want to filter
library/bootstrap noise themselves before replaying to a selected cursor for
frame inspection.

`retrace function-code` and the MCP tool `retrace_function_code` are
frame-scoped source helpers. Pass an application frame index, usually `0` after
`retrace diagnose`, and Retrace returns the containing function source with
`start_line`, `end_line`, `current_line`, truncation metadata, and a source
availability reason. This lets an agent inspect code for the stopped frame
without reading arbitrary paths.

`retrace eval` and the MCP tool `retrace_eval` evaluate one expression in a
selected application frame. Use them after `retrace_function_code` to inspect
the exact variables, attributes, item lookups, or assertion sub-expressions
shown by the source. Expressions should be read-only; results are bounded value
previews with type, truncation, and availability metadata.

MCP also exposes `retrace_agent_workflow`. Use it when an agent needs the
canonical debugging method before touching a recording. It returns the
evidence-first tool order:

```text
retrace_diagnose -> retrace_failures -> retrace_frame -> retrace_function_code
-> retrace_eval -> retrace_var -> retrace_provenance -> retrace_external_calls
```

It also returns rules for when the agent may claim root cause and a structured
root-cause report schema.

This workflow is for debugging the user's application failure inside a valid
recording. If Retrace replay itself crashes, consumes the wrong message, fails
before the expected application failure, or otherwise diverges from record, use
`retrace_replay_divergence_workflow` through MCP or the canonical loop in
`docs/REPLAY_DIVERGENCE_LOOP.md` instead. That workflow asks why replay stopped
consuming the same logical event stream that record produced.

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
