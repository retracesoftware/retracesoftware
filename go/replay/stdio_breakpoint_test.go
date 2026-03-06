package replay

import (
	"bufio"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// stdioReplay launches a Python replay with --stdio, writes all commands
// to stdin, closes it, then reads all JSON-line responses from stdout.
func stdioReplay(t *testing.T, python, trace, workDir string, commands []map[string]any) []map[string]any {
	t.Helper()

	var stdinData []byte
	for _, cmd := range commands {
		b, _ := json.Marshal(cmd)
		stdinData = append(stdinData, b...)
		stdinData = append(stdinData, '\n')
	}

	proc := exec.Command(python, "-m", "retracesoftware",
		"--recording", trace, "--stdio")
	proc.Dir = workDir
	proc.Stderr = os.Stderr

	stdinPipe, err := proc.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdoutPipe, err := proc.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := proc.Start(); err != nil {
		t.Fatalf("start replay: %v", err)
	}
	defer proc.Process.Kill()

	if _, err := stdinPipe.Write(stdinData); err != nil {
		t.Fatalf("write stdin: %v", err)
	}
	stdinPipe.Close()

	var msgs []map[string]any
	scanner := bufio.NewScanner(stdoutPipe)
	for scanner.Scan() {
		var msg map[string]any
		if err := json.Unmarshal(scanner.Bytes(), &msg); err != nil {
			t.Logf("non-JSON line: %s", scanner.Text())
			continue
		}
		t.Logf("recv: %s", scanner.Text())
		msgs = append(msgs, msg)
	}

	if err := proc.Wait(); err != nil {
		t.Logf("replay exited: %v (may be expected)", err)
	}
	return msgs
}

// TestStdioBreakpointScan records a script with --raw, then launches the
// Python replay directly with --stdio, sends hello + hit_breakpoints over
// stdin, and asserts that breakpoint_hit events come back on stdout.
//
// This bypasses all Go replay infrastructure (ControlClient, QueryEngine,
// Debugger, Proxy) and tests the Python breakpoint detection in isolation.
func TestStdioBreakpointScan(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping in short mode")
	}
	python := requirePython312(t)
	tmpDir := t.TempDir()

	script := filepath.Join(tmpDir, "target.py")
	if err := os.WriteFile(script, []byte("x = 1\nprint(x)\n"), 0644); err != nil {
		t.Fatal(err)
	}

	trace := filepath.Join(tmpDir, "trace.bin")
	cmd := exec.Command(python, "-m", "retracesoftware",
		"--recording", trace, "--raw", "--", script)
	cmd.Stderr = os.Stderr
	if out, err := cmd.Output(); err != nil {
		t.Fatalf("recording failed: %v\noutput: %s", err, out)
	}
	t.Logf("trace size: %d bytes", fileSize(trace))

	// --- Phase 1: scan for breakpoint hits ---
	msgs := stdioReplay(t, python, trace, tmpDir, []map[string]any{
		{"id": "1", "command": "hello"},
		{"id": "2", "command": "hit_breakpoints", "params": map[string]any{
			"breakpoint": map[string]any{"file": script, "line": 1},
		}},
	})

	var hitCursor map[string]any
	var gotHello, gotStop bool
	for _, msg := range msgs {
		if msg["id"] == "1" && msg["ok"] == true {
			gotHello = true
		}
		if kind, _ := msg["kind"].(string); kind == "event" {
			if ev, _ := msg["event"].(string); ev == "breakpoint_hit" {
				if payload, ok := msg["payload"].(map[string]any); ok {
					hitCursor = payload["cursor"].(map[string]any)
				}
			}
		}
		if kind, _ := msg["kind"].(string); kind == "stop" {
			gotStop = true
		}
	}

	if !gotHello {
		t.Fatal("phase 1: never received hello response")
	}
	if !gotStop {
		t.Fatal("phase 1: never received stop event")
	}
	if hitCursor == nil {
		t.Fatal("phase 1: no breakpoint_hit event with cursor")
	}
	t.Logf("phase 1 OK: hit cursor = %v", hitCursor)

	// --- Phase 2: start a fresh replay, run_to_cursor, then ask for stack ---
	t.Log("phase 2: starting fresh replay with run_to_cursor + stack")
	msgs2 := stdioReplay(t, python, trace, tmpDir, []map[string]any{
		{"id": "1", "command": "hello"},
		{"id": "2", "command": "run_to_cursor", "params": map[string]any{
			"cursor": hitCursor,
		}},
		{"id": "3", "command": "stack"},
	})

	var gotHello2, gotCursorStop, gotStack bool
	var stopReason string
	for _, msg := range msgs2 {
		if msg["id"] == "1" && msg["ok"] == true {
			gotHello2 = true
		}
		if kind, _ := msg["kind"].(string); kind == "stop" {
			gotCursorStop = true
			if payload, ok := msg["payload"].(map[string]any); ok {
				stopReason, _ = payload["reason"].(string)
			}
		}
		if msg["id"] == "3" && msg["ok"] == true {
			gotStack = true
			if result, ok := msg["result"].(map[string]any); ok {
				if frames, ok := result["frames"].([]any); ok {
					t.Logf("phase 2: stack has %d frames", len(frames))
					for i, f := range frames {
						if fm, ok := f.(map[string]any); ok {
							t.Logf("  frame %d: %v:%v %v", i,
								fm["filename"], fm["line"], fm["function"])
						}
					}
				}
			}
		}
	}

	if !gotHello2 {
		t.Fatal("phase 2: never received hello response")
	}
	if !gotCursorStop {
		t.Fatal("phase 2: never received stop event from run_to_cursor")
	}
	if stopReason != "cursor" {
		t.Fatalf("phase 2: expected stop reason 'cursor', got %q", stopReason)
	}
	if !gotStack {
		t.Fatal("phase 2: never received successful stack response")
	}
	t.Logf("phase 2 OK: run_to_cursor stopped with reason=%q, stack query succeeded", stopReason)
}

func fileSize(path string) int64 {
	st, err := os.Stat(path)
	if err != nil {
		return -1
	}
	return st.Size()
}
