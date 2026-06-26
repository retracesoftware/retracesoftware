# Retrace Replay Divergence Loop

This document is the canonical loop for debugging Retrace itself when record
and replay diverge. It is not the workflow for debugging a user's application
failure inside a good recording. For application-failure inspection, use the
agent workflow in `docs/AGENT_CONTEXT.md`.

## Core Position

A replay crash is usually a symptom. The root question is:

```text
Why did replay stop consuming the same logical event stream that record produced?
```

The final exception often names the object that happened to notice the bad
state. It does not prove where the stream first diverged.

## Required Evidence Packet

Every investigation should preserve these artifacts before editing code:

- fresh recording path
- fresh extract directory path
- record command, cwd, exit code, and log
- extract command, cwd, exit code, and log
- replay command, cwd, exit code, and log
- Python version and exact executable
- OS and architecture
- Retrace version or commit
- relevant dependency versions
- relevant env variables, with secrets redacted
- expected application failure, when there is one

Do not debug from stale extracted `.d/` directories. Regenerate the recording
and extraction unless the issue is specifically stale extraction.

## Freshness And Evidence Commands

Use commands like these as the starting point. Adjust the target command, but
preserve the shape: record fresh, extract fresh, replay fresh, and keep logs.

```bash
rm -rf /tmp/retrace-divergence-case
mkdir -p /tmp/retrace-divergence-case

RETRACE_DEBUG=1 python -m retracesoftware \
  --recording /tmp/retrace-divergence-case/case.retrace \
  --verbose \
  --stacktraces \
  -- \
  <target command> \
  > /tmp/retrace-divergence-case/record.stdout \
  2> /tmp/retrace-divergence-case/record.stderr

python -m retracesoftware \
  --recording /tmp/retrace-divergence-case/case.retrace \
  --list_pids \
  > /tmp/retrace-divergence-case/pids.txt

/tmp/retrace-divergence-case/case.retrace --extract \
  > /tmp/retrace-divergence-case/extract.stdout \
  2> /tmp/retrace-divergence-case/extract.stderr

ROOT_PID="$(head -n 1 /tmp/retrace-divergence-case/pids.txt)"
/tmp/retrace-divergence-case/case.d/${ROOT_PID}.bin \
  > /tmp/retrace-divergence-case/replay.stdout \
  2> /tmp/retrace-divergence-case/replay.stderr
```

If the recording uses the installed `replay` command instead of executable
recording files, keep the same evidence layout:

```bash
replay --recording /tmp/retrace-divergence-case/case.retrace --extract
replay /tmp/retrace-divergence-case/case.d/${ROOT_PID}.bin
```

Always capture the exact Python executable and package versions:

```bash
python -VV
python -c "import platform, sys; print(platform.platform()); print(sys.executable)"
python -m pip freeze
```

Search logs for mismatch markers only after the fresh run exists:

```bash
rg -n "Checkpoint difference|bind marker|ExpectedBindMarker|SYNC|CallMarkerMessage|Could not read|Traceback" \
  /tmp/retrace-divergence-case
```

## Decision Gates

- If replay reaches the same expected application failure, stop using this loop
  for that run and switch to the ordinary application-failure workflow.
- If replay fails before or differently from the expected application failure,
  continue this loop.
- If the only available trace is stale or was extracted by older code,
  regenerate before making any root-cause claim.
- If the failure disappears when regenerated, classify the old result as
  stale-extraction or packaging evidence, not as proof of a replay bug.

## The Loop

1. **Reproduce fresh.** Record, extract, and replay from scratch.
2. **Confirm the expected application failure.** If the app is supposed to fail
   with a pandas assertion, pytest failure, timeout, or other user error, write
   that down.
3. **Separate app failure from replay failure.** If replay reaches the same
   expected application failure, Retrace may be working for that scenario. If
   replay fails earlier or differently, continue.
4. **Find the earliest mismatch.** Compare record and replay at the logical
   stream boundary: next message, bind, checkpoint, callback, thread/process
   route, materialized object, or control-plane operation.
5. **Classify the mismatch.** Use one of the categories below.
6. **Reduce while preserving the first mismatch.** Removing the final library
   stack trace is fine; losing the first mismatch is not.
7. **Add a natural regression test.** The test should fail for the actual replay
   divergence, not because the test artificially asserts a made-up error.
8. **Fix the owning layer only.** Choose the narrowest layer that owns the
   violated contract.
9. **Verify.** Rerun the reduced repro, original repro, and relevant sentinel
   tests.
10. **Report root cause only with evidence.** A root-cause claim must cite the
    first mismatch and the contract that was violated.

## Mismatch Categories

- `boundary`: a nondeterministic external operation was missed, over-recorded,
  or recorded at the wrong layer.
- `binding/materialization`: replay bound or materialized the wrong object, too
  early, too late, twice, or on the wrong logical thread.
- `message-order`: replay expected one protocol event but read another, such as
  `SYNC`, `CALL`, `RESULT`, `CHECKPOINT`, callback, or bind marker drift.
- `control-plane`: debugger, extraction, logging, monitoring, AI tooling, or
  replay plumbing was retraced instead of bypassing gates.
- `scheduling`: replay changed thread wakeup, lock, condition, queue, async, or
  event-loop order.
- `pathpredicate/fd-provenance`: a file path was passthrough but fd-level calls
  on its descriptor were still retraced, or the reverse.
- `finalizer/GC`: object lifetime, weakref, destructor, traceback retention, or
  shutdown ordering changed message timing.
- `subprocess/fork/thread`: child process, fork, or logical thread routing did
  not match record.
- `packaging`: editable/wheel layout, replay binary discovery, entrypoints,
  checksums, or extraction paths changed behavior before replay semantics.

## Owning Layer Ladder

Prefer the narrowest responsible layer:

1. `src/retracesoftware/modules/*.toml`
   - Use when a nondeterministic function/type needs interception or when a
     framework control-plane function should be disabled.
2. `src/retracesoftware/install/`
   - Use when runtime patching, startup, auto-enable, pytest integration, or
     path/fd provenance owns the mismatch.
3. `src/retracesoftware/proxy/`
   - Use when gate, phase, binding, materialization, callback routing, or
     message-order contracts are violated.
4. `src/retracesoftware/stream/`, `src/retracesoftware/protocol/`, `cpp/stream/`
   - Use when serialization, demux, pid/thread framing, or wire transport owns
     the mismatch.
5. `go/replay/` or `src/retracesoftware/dap/`
   - Use when extraction, workspace generation, DAP, cursor, breakpoint, or
     debugger control flow owns the mismatch.

If a fix touches multiple layers, explain why the lower layer could not own it.

## Regression Rules

Good regressions:

- create a fresh recording
- extract fresh replay artifacts
- replay naturally
- assert the replay reaches the expected application result or expected
  application failure
- fail before the fix with the real divergence
- use the smallest reproducer that preserves the first mismatch

Bad regressions:

- assert a hard-coded final exception without proving it is the first mismatch
- reuse an old `.d/` extraction directory
- call lower-level user functions instead of the command that reproduces the
  divergence
- require live external services during replay, unless the bug is specifically
  that replay incorrectly touches them

## Evidence Ledger Template

Keep this ledger in the issue or investigation notes for every serious replay
bug:

```text
status:
record command:
extract command:
replay command:
python executable:
python version:
os / arch:
dependency versions:
expected application failure:
actual replay failure:
does replay fail before expected failure:
record next logical event:
replay expected next logical event:
replay consumed next logical event:
first mismatch:
classification:
owning layer:
smallest reproducer:
regression test:
sentinel tests:
remaining uncertainty:
```

## Issue Template Checklist

Every replay-divergence issue should answer:

- What is the expected application failure?
- Does replay fail before that failure?
- What fresh commands produced the recording, extraction, and replay?
- What is the first observed record/replay mismatch?
- Which category owns the mismatch?
- What reduced repro preserves the mismatch?
- What regression test was added or proposed?
- Which sentinel tests protect the owning layer?

## Example: SQLAlchemy / pyodbc Seed Divergence

For an issue such as macOS Python 3.12 SQLAlchemy/pyodbc seed replay
divergence, the useful application failure may be a pandas assertion. A
`RuntimeError: bind marker returned when bind was expected` during replay is
not the root cause by itself. The loop must determine why replay consumed a
bind marker at that point:

- Did record produce a cursor/connection operation while replay expected a bind?
- Did a SQLAlchemy pool/event/lock path emit different control-plane events?
- Did a pyodbc returned object materialize as the wrong DBAPI concept?
- Did path, finalizer, or platform-specific object lifetime change message
  order?

Only after answering that first-mismatch question should the issue claim root
cause or propose a fix.

For this class of issue, a weak report says:

```text
SQLAlchemy crashed with RuntimeError: bind marker returned when bind was expected.
```

A useful report says:

```text
Expected app failure: pandas assertion in the test.
Replay failed first: yes.
Record next event: <specific connection/cursor/bind/checkpoint event>.
Replay expected: <specific bind/message/checkpoint>.
Replay consumed: <specific wrong bind/message/checkpoint>.
Classification: binding/materialization or message-order.
Owning layer: <module config/proxy/stream/go replay/etc>.
Regression: <test path> fails naturally before the fix.
```
