package replay

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// TestStdioThreadBreakpoint records a multi-threaded Python script with --raw,
// replays it with --stdio, sets a breakpoint inside the worker function, and
// asserts that breakpoint_hit events arrive from multiple threads.
func TestStdioThreadBreakpoint(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping in short mode")
	}
	python := requirePython312(t)
	tmpDir := t.TempDir()

	// Copy the target script into the temp dir so paths are deterministic.
	scriptSrc := filepath.Join("..", "..", "examples", "target_threads.py")
	scriptBytes, err := os.ReadFile(scriptSrc)
	if err != nil {
		t.Fatalf("read target script: %v", err)
	}
	script := filepath.Join(tmpDir, "target_threads.py")
	if err := os.WriteFile(script, scriptBytes, 0644); err != nil {
		t.Fatal(err)
	}

	// --- Record ---
	trace := filepath.Join(tmpDir, "trace.bin")
	rec := exec.Command(python, "-m", "retracesoftware",
		"--recording", trace, "--raw", "--", script)
	rec.Stderr = os.Stderr
	if out, err := rec.Output(); err != nil {
		t.Fatalf("recording failed: %v\noutput: %s", err, out)
	}
	t.Logf("trace size: %d bytes", fileSize(trace))

	// --- Replay: scan for breakpoint hits on "total += i" (line 8) ---
	// We ask for up to 100 hits so we can see hits from all 3 worker threads.
	msgs := stdioReplay(t, python, trace, tmpDir, []map[string]any{
		{"id": "1", "command": "hello"},
		{"id": "2", "command": "hit_breakpoints", "params": map[string]any{
			"breakpoint": map[string]any{"file": script, "line": 8},
			"max_hits":   100,
		}},
	})

	threadIDs := map[any]int{}
	for _, msg := range msgs {
		kind, _ := msg["kind"].(string)
		if kind != "event" {
			continue
		}
		ev, _ := msg["event"].(string)
		if ev != "breakpoint_hit" {
			continue
		}
		payload, _ := msg["payload"].(map[string]any)
		if payload == nil {
			continue
		}
		cursor, _ := payload["cursor"].(map[string]any)
		if cursor == nil {
			continue
		}
		tid := cursor["thread_id"]
		threadIDs[tid]++
	}

	t.Logf("breakpoint_hit events by thread_id: %v", threadIDs)

	if len(threadIDs) == 0 {
		t.Fatal("no breakpoint_hit events received")
	}

	// The 3 workers each loop different counts (10, 11, 12 iterations),
	// so we expect hits from at least 2 distinct threads (conservatively;
	// in practice all 3 should appear).
	if len(threadIDs) < 2 {
		t.Errorf("expected breakpoint hits from multiple threads, got %d distinct thread_id(s): %v",
			len(threadIDs), threadIDs)
	}
}
