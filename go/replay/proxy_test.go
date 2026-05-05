package replay

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

type fakeDAPDebugger struct {
	nextID  int
	added   []BreakpointSpec
	removed []int
	hits    *HitList
}

func (d *fakeDAPDebugger) AddBreakpoint(ctx context.Context, spec BreakpointSpec) (int, error) {
	d.nextID++
	d.added = append(d.added, spec)
	return d.nextID, nil
}

func (d *fakeDAPDebugger) RemoveBreakpoint(id int) {
	d.removed = append(d.removed, id)
}

func (d *fakeDAPDebugger) Hits() *HitList {
	if d.hits == nil {
		d.hits = NewHitList()
	}
	return d.hits
}

func (d *fakeDAPDebugger) WaitForScans(ctx context.Context) error { return nil }

func (d *fakeDAPDebugger) Close() error { return nil }

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

func TestSetBreakpointsReplacesOnlyMatchingSource(t *testing.T) {
	debugger := &fakeDAPDebugger{}
	proxy := &Proxy{
		debugger:        debugger,
		breakpointIDs:   make(map[string]int),
		breakpointSpecs: make(map[string]BreakpointSpec),
	}

	mainPath := filepath.Join(t.TempDir(), "main.py")
	servicePath := filepath.Join(t.TempDir(), "service.py")

	if _, err := proxy.handleSetBreakpoints(mustSetBreakpointsArgs(t, mainPath, 9)); err != nil {
		t.Fatalf("set main breakpoints: %v", err)
	}
	if _, err := proxy.handleSetBreakpoints(mustSetBreakpointsArgs(t, servicePath, 17)); err != nil {
		t.Fatalf("set service breakpoints: %v", err)
	}

	if len(debugger.added) != 2 {
		t.Fatalf("added breakpoints = %d, want 2", len(debugger.added))
	}
	if len(debugger.removed) != 0 {
		t.Fatalf("removed after second source = %v, want none", debugger.removed)
	}
	if len(proxy.breakpointIDs) != 2 {
		t.Fatalf("tracked breakpoints = %d, want 2", len(proxy.breakpointIDs))
	}

	if _, err := proxy.handleSetBreakpoints(mustSetBreakpointsArgs(t, mainPath)); err != nil {
		t.Fatalf("clear main breakpoints: %v", err)
	}

	if len(debugger.removed) != 1 || debugger.removed[0] != 1 {
		t.Fatalf("removed after clearing main = %v, want [1]", debugger.removed)
	}
	if len(proxy.breakpointIDs) != 1 {
		t.Fatalf("tracked breakpoints after clearing main = %d, want 1", len(proxy.breakpointIDs))
	}
	if _, ok := proxy.breakpointIDs[breakpointKey(BreakpointSpec{File: servicePath, Line: 17})]; !ok {
		t.Fatalf("service breakpoint was removed")
	}
}

func mustSetBreakpointsArgs(t *testing.T, path string, lines ...int) json.RawMessage {
	t.Helper()
	breakpoints := make([]map[string]any, 0, len(lines))
	for _, line := range lines {
		breakpoints = append(breakpoints, map[string]any{"line": line})
	}
	raw, err := json.Marshal(map[string]any{
		"source":      map[string]any{"path": path},
		"breakpoints": breakpoints,
	})
	if err != nil {
		t.Fatal(err)
	}
	return raw
}
