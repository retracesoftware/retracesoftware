# Claims

## Safe Claim

Retrace replay was used to reproduce and inspect `django-modern-rest #1048`.
Manual VS Code replay showed that query parameter metadata referenced
`#/components/schemas/TestEnum`, while the OpenAPI schema registry did not
contain `TestEnum`, causing `KeyError: "TestEnum"` during reference resolution.

## Important Limitation

The current agent-facing CLI/MCP inspection path did not expose the useful
final failure state for this case. It stopped on an earlier internal generated
`TypeError`. The useful evidence was obtained through manual VS Code replay.

## Unsafe Claims

Do not claim:

- Retrace automatically diagnosed the root cause.
- The agent/MCP workflow found or solved this bug.
- CLI/MCP successfully exposed the useful final failure state.
- This proves benchmark uplift.
- This bug was impossible to solve without Retrace.
- This demonstrates full value-level provenance.
