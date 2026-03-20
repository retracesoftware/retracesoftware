package replay

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestRoundtrip records a script through a FIFO, relays the first
// PID's unframed stream to a second FIFO, and runs the replay with
// --format unframed_binary.
func TestRoundtrip(t *testing.T) {
	_ = requirePython(t)

	tmpDir := t.TempDir()
	script := filepath.Join(tmpDir, "hello.py")
	if err := os.WriteFile(script, []byte("print('roundtrip ok')\n"), 0644); err != nil {
		t.Fatal(err)
	}

	var stdout, stderr bytes.Buffer
	if err := Roundtrip(script, &stdout, &stderr); err != nil {
		t.Fatalf("Roundtrip failed: %v\nstderr: %s", err, stderr.String())
	}

	got := strings.TrimSpace(stdout.String())
	if got == "" {
		t.Fatal("expected process info JSON from roundtrip, got empty output")
	}
	if !strings.Contains(got, "executable") {
		t.Errorf("expected 'executable' in process info JSON, got %q", got)
	}
	t.Logf("roundtrip process info: %s", got)
}
