package replay

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
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

type failingSnapshotProvider struct{}

func (f failingSnapshotProvider) ClosestBeforeCall(context.Context, uint64, FunctionCounts) (*Snapshot, error) {
	return nil, errors.New("snapshot unavailable in unit test")
}

func (f failingSnapshotProvider) ClosestBeforeReturn(context.Context, uint64, FunctionCounts) (*Snapshot, error) {
	return nil, errors.New("snapshot unavailable in unit test")
}

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

func TestContinueAdvancesWithinSameTraceMessage(t *testing.T) {
	debugger := &fakeDAPDebugger{}
	first := BreakpointHit{
		BreakpointID: 1,
		Spec:         BreakpointSpec{File: "target.py", Line: 1},
		Location: Location{
			MessageIndex:   1483,
			ThreadID:       1,
			FunctionCounts: FunctionCounts{1, 1},
		},
	}
	second := BreakpointHit{
		BreakpointID: 2,
		Spec:         BreakpointSpec{File: "target.py", Line: 2},
		Location: Location{
			MessageIndex:   1483,
			ThreadID:       1,
			FunctionCounts: FunctionCounts{1, 2},
		},
	}
	debugger.Hits().Insert(first)
	debugger.Hits().Insert(second)

	var out bytes.Buffer
	proxy := &Proxy{
		debugger:            debugger,
		provider:            failingSnapshotProvider{},
		clientW:             NewWriter(&out),
		currentMessageIndex: first.Location.MessageIndex,
		currentCursor:       NewCursor(first.Location, nil, nil),
	}

	if err := proxy.handleContinue(context.Background(), false); err != nil {
		t.Fatalf("continue: %v", err)
	}
	raw, err := ReadMessage(bufio.NewReader(&out))
	if err != nil {
		t.Fatalf("read DAP message: %v", err)
	}
	var msg map[string]any
	if err := json.Unmarshal(raw, &msg); err != nil {
		t.Fatalf("unmarshal DAP message: %v", err)
	}
	if msg["event"] != "stopped" {
		t.Fatalf("continue event = %v, want stopped; message: %s", msg["event"], raw)
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
