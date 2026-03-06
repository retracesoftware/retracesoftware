package replay

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPrepareCacheCreatesIndex(t *testing.T) {
	python := requirePython(t)
	tmp := t.TempDir()

	script := filepath.Join(tmp, "hello.py")
	os.WriteFile(script, []byte("print('hello')\n"), 0644)

	trace := filepath.Join(tmp, "trace.retrace")
	recordTrace(t, python, script, trace)

	idx, cacheDir, err := PrepareCache(trace)
	if err != nil {
		t.Fatal(err)
	}

	if idx.Root == nil {
		t.Fatal("index root is nil")
	}
	if idx.Root.Type != "exec" {
		t.Fatalf("root type: expected exec, got %q", idx.Root.Type)
	}

	indexPath := filepath.Join(cacheDir, "index.json")
	if _, err := os.Stat(indexPath); err != nil {
		t.Fatalf("index.json not found: %v", err)
	}

	// Leaf file should exist.
	leafPath := filepath.Join(cacheDir, idStr(idx.Root.PID)+".bin")
	if _, err := os.Stat(leafPath); err != nil {
		t.Fatalf("leaf file not found: %v", err)
	}

	t.Logf("cache dir: %s", cacheDir)
}

func TestPrepareCacheHitsOnSecondRun(t *testing.T) {
	python := requirePython(t)
	tmp := t.TempDir()

	script := filepath.Join(tmp, "hello.py")
	os.WriteFile(script, []byte("print('cached')\n"), 0644)

	trace := filepath.Join(tmp, "trace.retrace")
	recordTrace(t, python, script, trace)

	_, cacheDir1, err := PrepareCache(trace)
	if err != nil {
		t.Fatal(err)
	}

	// Second call should hit cache.
	_, cacheDir2, err := PrepareCache(trace)
	if err != nil {
		t.Fatal(err)
	}

	if cacheDir1 != cacheDir2 {
		t.Fatalf("cache dirs differ: %s vs %s", cacheDir1, cacheDir2)
	}
}

func TestReplayAllSingleProcess(t *testing.T) {
	python := requirePython(t)
	tmp := t.TempDir()

	script := filepath.Join(tmp, "hello.py")
	os.WriteFile(script, []byte("print('replay-all-ok')\n"), 0644)

	trace := filepath.Join(tmp, "trace.retrace")
	recordTrace(t, python, script, trace)

	var stdout, stderr bytes.Buffer
	if err := ReplayAll(trace, &stdout, &stderr); err != nil {
		t.Fatalf("ReplayAll: %v\nstderr: %s", err, stderr.String())
	}

	if !strings.Contains(stdout.String(), "replay-all-ok") {
		t.Fatalf("expected 'replay-all-ok' in output, got: %s", stdout.String())
	}
}

func TestReplayAllWithForks(t *testing.T) {
	python := requirePython(t)
	tmp := t.TempDir()

	script := filepath.Join(tmp, "forktest.py")
	os.WriteFile(script, []byte(`import os, sys, time

pids = []
for i in range(2):
    pid = os.fork()
    if pid == 0:
        print(f"child-{i}")
        time.sleep(0.01)
        sys.exit(0)
    pids.append(pid)

for p in pids:
    os.waitpid(p, 0)
print("parent-done")
`), 0644)

	trace := filepath.Join(tmp, "trace.retrace")
	recordTrace(t, python, script, trace)

	var stdout, stderr bytes.Buffer
	err := ReplayAll(trace, &stdout, &stderr)
	t.Logf("stdout:\n%s", stdout.String())
	t.Logf("stderr:\n%s", stderr.String())

	if err != nil {
		t.Fatalf("ReplayAll: %v", err)
	}
}

func idStr(pid uint32) string {
	return fmt.Sprintf("%d", pid)
}
