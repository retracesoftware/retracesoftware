---
name: retrace-replay-divergence-investigator
description: "Debug Retrace itself when record/replay diverges. Use for replay crashes, bind marker mismatches, checkpoint/SYNC differences, wrong replayed object types, stale extraction suspicion, pytest/DAP replay desyncs, subprocess/fork/thread replay issues, pathpredicate/fd provenance problems, or any bug where Retrace replay fails before the expected user application failure. This skill is for finding the first record/replay mismatch, not for debugging the user's application."
---

# Retrace Replay Divergence Investigator

Use this skill when Retrace replay itself is suspect. Do not stop at the final
exception. A replay crash is usually a downstream symptom of an earlier stream,
boundary, binding, scheduling, or control-plane divergence.

Core question:

```text
Why did replay stop consuming the same logical event stream that record produced?
```

Before changing code, read the relevant local `AGENTS.md`. If the investigation
touches `src/retracesoftware/proxy/`, read
`src/retracesoftware/proxy/AGENTS.md` and
`src/retracesoftware/proxy/DESIGN.md`, then name the violated design contract.
For full rationale and examples, see `docs/REPLAY_DIVERGENCE_LOOP.md`.

## Required Loop

1. Reproduce with a fresh recording. Do not reuse stale extracted `.d/`
   directories.
2. Preserve the evidence packet: record log, extract log, replay log, Python
   version, OS, package versions, command, cwd, and relevant env.
3. Confirm whether the application failure is expected.
4. Decide whether replay fails before that expected application failure.
5. Locate the earliest mismatch, not the final stack trace.
6. Classify the mismatch:
   `boundary`, `binding/materialization`, `message-order`, `control-plane`,
   `scheduling`, `pathpredicate/fd-provenance`, `finalizer/GC`,
   `subprocess/fork/thread`, `packaging`, or `unknown`.
7. Reduce the repro while preserving the first mismatch.
8. Add a regression test that fails naturally. Do not force failure with a
   synthetic assertion unless the assertion checks the real replay outcome.
9. Fix only the owning layer. Prefer module config or a disabled framework
   control-plane path when that expresses the actual boundary.
10. Verify the reduced repro, the original repro, and the relevant sentinel
    tests.
11. Report root cause only with evidence from the first mismatch.

## Decision Gates

- If replay reaches the same expected application failure, stop using this
  skill for that run and switch to the normal application-failure workflow.
- If replay fails before or differently from the expected application failure,
  continue this skill.
- If the only trace or extracted directory is stale, regenerate it before
  making a root-cause claim.
- If a fresh run no longer reproduces the failure, classify the old evidence as
  stale-extraction or packaging evidence rather than a confirmed replay bug.

## Evidence Commands

Preserve a fresh evidence directory before editing code:

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

Also capture:

```bash
python -VV
python -c "import platform, sys; print(platform.platform()); print(sys.executable)"
python -m pip freeze
```

## Mismatch Evidence

For every iteration, keep a short ledger:

```text
iteration:
record command:
extract command:
replay command:
expected app failure:
actual replay failure:
first observed mismatch:
classification:
hypothesis:
test performed:
result:
next step:
```

The first mismatch should answer:

- What logical event did record produce next?
- What logical event did replay attempt to consume?
- Which gate, phase, message, binding, materialization, thread, process, or
  control-plane path made them differ?

## Anti-Patterns

- Do not treat the final stack trace as root cause.
- Do not patch the user app or third-party library symptom before finding the
  record/replay mismatch.
- Do not add broad edge-case code if config can disable/control the framework
  path.
- Do not proxy returned data types unless they are the true external boundary.
- Do not let replay call live external code to get past a failure.
- Do not use stale extracted recordings.

## Report Shape

When reporting back, use this shape:

```text
status:
expected application failure:
replay failure before expected failure:
first mismatch:
classification:
smallest reproducer:
regression test:
owning layer:
fix summary:
validation:
remaining risk:
```
