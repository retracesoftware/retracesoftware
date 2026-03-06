package replay

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// requirePython returns a Python path or skips the test.
func requirePython(t *testing.T) string {
	t.Helper()
	p, err := findPython()
	if err != nil {
		t.Skip(err)
	}
	return p
}

// recordTrace records a small Python script and returns the trace path.
func recordTrace(t *testing.T, python, script, tracePath string) {
	t.Helper()
	cmd := exec.Command(python, "-m", "retracesoftware",
		"--recording", tracePath, "--", script)
	cmd.Stderr = os.Stderr
	if out, err := cmd.Output(); err != nil {
		t.Fatalf("recording failed: %v\noutput: %s", err, out)
	}
	if stat, err := os.Stat(tracePath); err != nil || stat.Size() == 0 {
		t.Fatal("trace file missing or empty after recording")
	}
}

// TestReplayOnlyListPIDs records a script, then uses the Go pipeline
// (demux -> resolve process -> launch Python) to list PIDs from the
// trace.  This exercises the full record-then-replay infrastructure
// without depending on the full replay engine.
func TestReplayOnlyListPIDs(t *testing.T) {
	python := requirePython(t)
	tmpDir := t.TempDir()

	script := filepath.Join(tmpDir, "hello.py")
	if err := os.WriteFile(script, []byte("print('hello from replay')\n"), 0644); err != nil {
		t.Fatal(err)
	}

	trace := filepath.Join(tmpDir, "trace.bin")
	recordTrace(t, python, script, trace)

	pid, err := FirstPID(trace)
	if err != nil {
		t.Fatalf("FirstPID: %v", err)
	}

	var stdout, stderr bytes.Buffer
	if err := ReplayOnly(trace, pid, &stdout, &stderr, "--list_pids"); err != nil {
		t.Fatalf("ReplayOnly --list_pids failed: %v\nstderr: %s", err, stderr.String())
	}

	got := strings.TrimSpace(stdout.String())
	if got == "" {
		t.Fatal("expected at least one PID in output, got empty string")
	}

	t.Logf("PIDs from trace: %s", got)
}

// TestResolveProcess verifies that ResolveProcess can demux a trace
// and extract process info with the expected fields.
func TestResolveProcess(t *testing.T) {
	python := requirePython(t)
	tmpDir := t.TempDir()

	script := filepath.Join(tmpDir, "noop.py")
	if err := os.WriteFile(script, []byte("pass\n"), 0644); err != nil {
		t.Fatal(err)
	}

	trace := filepath.Join(tmpDir, "trace.bin")
	recordTrace(t, python, script, trace)

	pid, err := FirstPID(trace)
	if err != nil {
		t.Fatalf("FirstPID: %v", err)
	}

	process, dir, err := ResolveProcess(trace, pid)
	if err != nil {
		t.Fatalf("ResolveProcess: %v", err)
	}
	defer os.RemoveAll(dir)

	for _, key := range []string{"executable", "encoding_version", "recording"} {
		if _, ok := process[key]; !ok {
			t.Errorf("process dict missing %q", key)
		}
	}
}
