"""End-to-end test of the stdio DAP transport.

Spawns 'python -m retracesoftware.dap' as a subprocess (no RETRACE_DAP_SOCKET),
talks DAP over its stdin/stdout, launches a target that prints to stdout,
and verifies the output arrives as DAP output events.
"""

import json
import os
import subprocess
import sys

ADAPTER = [sys.executable, "-S", "-m", "retracesoftware.dap"]
TARGET = os.path.join(os.path.dirname(__file__), "target_hello.py")
SRC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")


def write_dap(proc, seq, command, arguments=None):
    msg = {"seq": seq, "type": "request", "command": command}
    if arguments:
        msg["arguments"] = arguments
    body = json.dumps(msg, separators=(",", ":")).encode()
    header = f"Content-Length: {len(body)}\r\n\r\n".encode()
    proc.stdin.write(header + body)
    proc.stdin.flush()


def read_dap(proc):
    content_length = -1
    while True:
        line = proc.stdout.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if line.startswith(b"Content-Length:"):
            content_length = int(line.split(b":")[1].strip())
    if content_length < 0:
        return None
    body = proc.stdout.read(content_length)
    return json.loads(body)


def main():
    env = {**os.environ, "PYTHONPATH": SRC}
    # No RETRACE_DAP_SOCKET → stdio mode
    env.pop("RETRACE_DAP_SOCKET", None)

    proc = subprocess.Popen(
        ADAPTER,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    seq = 0

    def send(cmd, args=None):
        nonlocal seq
        seq += 1
        write_dap(proc, seq, cmd, args)

    # Initialize
    send("initialize", {"clientID": "test", "adapterID": "retrace"})
    msgs = [read_dap(proc), read_dap(proc)]
    for m in msgs:
        if m.get("type") == "event" and m.get("event") == "initialized":
            print("OK: initialized event")
        elif m.get("type") == "response" and m.get("command") == "initialize":
            assert m["success"]
            print("OK: initialize response")

    # Launch target
    send("launch", {"program": TARGET, "stopOnEntry": False})
    resp = read_dap(proc)
    assert resp["success"], f"launch failed: {resp}"
    print("OK: launch response")

    # Collect events until terminated
    got_stdout = False
    got_exited = False
    got_terminated = False
    output_text = ""

    for _ in range(20):
        m = read_dap(proc)
        if m is None:
            break
        if m.get("type") == "event":
            evt = m["event"]
            if evt == "output":
                cat = m["body"].get("category", "")
                text = m["body"].get("output", "")
                output_text += text
                if cat == "stdout":
                    got_stdout = True
            elif evt == "exited":
                got_exited = True
                print(f"OK: exited code={m['body']['exitCode']}")
            elif evt == "terminated":
                got_terminated = True
                print("OK: terminated")
                break
            elif evt == "stopped":
                # If stop-on-entry fires, just continue
                send("continue", {"threadId": m["body"]["threadId"]})
                read_dap(proc)  # continue response

    assert got_stdout, f"Did not receive stdout output event. Collected: {output_text!r}"
    assert "result = 30" in output_text, f"Expected 'result = 30' in output, got: {output_text!r}"
    print(f"OK: captured stdout: {output_text.strip()!r}")
    assert got_exited, "Missing exited event"
    assert got_terminated, "Missing terminated event"

    send("disconnect")
    read_dap(proc)

    proc.wait(timeout=3)
    print(f"OK: process exited with code {proc.returncode}")
    print("ALL STDIO TESTS PASSED")


if __name__ == "__main__":
    main()
