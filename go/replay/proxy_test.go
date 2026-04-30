package replay

import (
	"os"
	"path/filepath"
	"testing"
)

func TestResolveSourcePathPrefersRecordedProcessCWD(t *testing.T) {
	tmpDir := t.TempDir()
	recordingDir := filepath.Join(tmpDir, "generated-workspace")
	processCWD := filepath.Join(tmpDir, "project")
	relPath := filepath.Join("examples", "target_hello.py")

	recordingPath := filepath.Join(recordingDir, relPath)
	if err := os.MkdirAll(filepath.Dir(recordingPath), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(recordingPath, []byte("print('wrong')\n"), 0644); err != nil {
		t.Fatal(err)
	}

	processPath := filepath.Join(processCWD, relPath)
	if err := os.MkdirAll(filepath.Dir(processPath), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(processPath, []byte("print('right')\n"), 0644); err != nil {
		t.Fatal(err)
	}

	proxy := &Proxy{recordingDir: recordingDir, processCWD: processCWD}
	if got := proxy.resolveSourcePath(relPath); got != processPath {
		t.Fatalf("resolveSourcePath(%q) = %q, want %q", relPath, got, processPath)
	}
}

func TestResolveSourcePathFallsBackToRecordedProcessCWD(t *testing.T) {
	tmpDir := t.TempDir()
	processCWD := filepath.Join(tmpDir, "project")
	relPath := filepath.Join("examples", "missing.py")

	proxy := &Proxy{
		recordingDir: filepath.Join(tmpDir, "generated-workspace"),
		processCWD:   processCWD,
	}
	want := filepath.Join(processCWD, relPath)
	if got := proxy.resolveSourcePath(relPath); got != want {
		t.Fatalf("resolveSourcePath(%q) = %q, want %q", relPath, got, want)
	}
}
