package replay

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestRoundtripThreads records examples/target_threads.py with
// --format unframed_binary, then
// replays it with --stdio (hello + close), verifying that a simple
// multi-threaded program survives a record/replay roundtrip.
func TestRoundtripThreads(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping in short mode")
	}
	python := requirePython312(t)

	scriptSrc := filepath.Join("..", "..", "examples", "target_threads.py")
	scriptBytes, err := os.ReadFile(scriptSrc)
	if err != nil {
		t.Fatalf("read target script: %v", err)
	}

	tmpDir := t.TempDir()
	script := filepath.Join(tmpDir, "target_threads.py")
	if err := os.WriteFile(script, scriptBytes, 0644); err != nil {
		t.Fatal(err)
	}

	// --- Step 1: record ---
	trace := filepath.Join(tmpDir, "trace.bin")
	rec := pythonCommand(python, "-m", "retracesoftware",
		"--recording", trace, "--format", "unframed_binary", "--", script)
	rec.Dir = tmpDir
	var recStderr strings.Builder
	rec.Stderr = &recStderr
	if out, err := rec.Output(); err != nil {
		t.Fatalf("record failed: %v\nstdout: %s\nstderr: %s", err, out, recStderr.String())
	}
	t.Logf("trace size: %d bytes", fileSize(trace))

	// --- Step 2: replay with --stdio, send hello + close ---
	msgs := stdioReplay(t, python, trace, tmpDir, []map[string]any{
		{"id": "1", "command": "hello"},
		{"id": "2", "command": "close"},
	})

	var gotHello, gotClose bool
	for _, msg := range msgs {
		if msg["id"] == "1" && msg["ok"] == true {
			gotHello = true
		}
		if msg["id"] == "2" && msg["ok"] == true {
			gotClose = true
		}
	}

	if !gotHello {
		t.Fatal("never received hello response")
	}
	if !gotClose {
		t.Fatal("never received close response")
	}
	t.Log("roundtrip OK: hello + close succeeded for multi-threaded script")
}
