package replay

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"sync"
	"testing"
	"time"
)

func writeTempPidFile(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	p := filepath.Join(dir, "test.bin")
	preamble := `{"executable":"/usr/bin/python3","cwd":"/tmp","type":"exec"}` + "\n"
	if err := os.WriteFile(p, []byte(preamble), 0644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestRunReplayStartsFreshEachCall(t *testing.T) {
	oldBuild := buildCommand
	oldRun := runCommand
	defer func() {
		buildCommand = oldBuild
		runCommand = oldRun
	}()

	var built [][]string
	buildCommand = func(name string, args ...string) *exec.Cmd {
		row := []string{name}
		row = append(row, args...)
		built = append(built, row)
		return &exec.Cmd{}
	}
	runCommand = func(cmd *exec.Cmd) error { return nil }

	pidFile := writeTempPidFile(t)

	if err := RunReplay(pidFile, nil, nil, 0, "--verbose"); err != nil {
		t.Fatalf("first run: %v", err)
	}
	if err := RunReplay(pidFile, nil, nil, 0, "--verbose"); err != nil {
		t.Fatalf("second run: %v", err)
	}

	if len(built) != 2 {
		t.Fatalf("buildCommand calls = %d, want 2", len(built))
	}
	for i, row := range built {
		if len(row) < 5 {
			t.Fatalf("command %d too short: %v", i, row)
		}
		if row[0] != "/usr/bin/python3" {
			t.Fatalf("command %d python = %q, want /usr/bin/python3", i, row[0])
		}
		if !slices.Contains(row, "--recording") || !slices.Contains(row, pidFile) {
			t.Fatalf("command %d missing recording args: %v", i, row)
		}
	}
}

func TestBreakpointSpecArg(t *testing.T) {
	arg, err := (BreakpointSpec{
		File: "/tmp/a.py",
		Line: 42,
	}).Arg()
	if err != nil {
		t.Fatalf("Arg: %v", err)
	}
	if arg != "/tmp/a.py:42" {
		t.Fatalf("arg=%q", arg)
	}

	arg, err = (BreakpointSpec{
		File:      "/tmp/a.py",
		Line:      42,
		Condition: "x > 1",
	}).Arg()
	if err != nil {
		t.Fatalf("Arg with condition: %v", err)
	}
	if arg != "/tmp/a.py:42:x > 1" {
		t.Fatalf("arg=%q", arg)
	}
}

func TestParseLocationFromJSON(t *testing.T) {
	line, _ := json.Marshal(map[string]any{
		"cursor": map[string]any{
			"thread_id":       1,
			"function_counts": []int{1, 2, 3},
			"f_lasti":         42,
		},
	})
	loc, err := parseLocationFromJSON(line)
	if err != nil {
		t.Fatalf("parseLocationFromJSON: %v", err)
	}
	if loc.ThreadID != 1 {
		t.Fatalf("thread_id = %d, want 1", loc.ThreadID)
	}
	if len(loc.FunctionCounts) != 3 || loc.FunctionCounts[0] != 1 || loc.FunctionCounts[2] != 3 {
		t.Fatalf("unexpected function_counts: %#v", loc.FunctionCounts)
	}
	if loc.FLasti == nil || *loc.FLasti != 42 {
		t.Fatalf("unexpected f_lasti: %v", loc.FLasti)
	}
}

func TestParseLocationFromJSON_NoFLasti(t *testing.T) {
	line, _ := json.Marshal(map[string]any{
		"cursor": map[string]any{
			"thread_id":       5,
			"function_counts": []int{10, 20},
		},
	})
	loc, err := parseLocationFromJSON(line)
	if err != nil {
		t.Fatalf("parseLocationFromJSON: %v", err)
	}
	if loc.ThreadID != 5 {
		t.Fatalf("thread_id = %d, want 5", loc.ThreadID)
	}
	if loc.FLasti != nil {
		t.Fatalf("f_lasti should be nil, got %v", *loc.FLasti)
	}
}

func TestLocationJSONRoundTrip(t *testing.T) {
	fLasti := 30
	orig := Location{
		ThreadID:       1,
		FunctionCounts: []int{10, 20, 30},
		FLasti:         &fLasti,
		MessageIndex:   42,
	}
	data, err := json.Marshal(orig)
	if err != nil {
		t.Fatalf("MarshalJSON: %v", err)
	}

	var loaded Location
	if err := json.Unmarshal(data, &loaded); err != nil {
		t.Fatalf("UnmarshalJSON: %v", err)
	}
	if loaded.MessageIndex != 42 {
		t.Fatalf("message index = %d, want 42", loaded.MessageIndex)
	}
	if loaded.ThreadID != 1 {
		t.Fatalf("thread_id = %d, want 1", loaded.ThreadID)
	}
	if len(loaded.FunctionCounts) != 3 || loaded.FunctionCounts[1] != 20 {
		t.Fatalf("function_counts mismatch: %v", loaded.FunctionCounts)
	}
	if loaded.FLasti == nil || *loaded.FLasti != 30 {
		t.Fatalf("f_lasti mismatch: %v", loaded.FLasti)
	}
}

func TestLocationJSONRoundTrip_NilFLasti(t *testing.T) {
	orig := Location{
		ThreadID:       2,
		FunctionCounts: []int{5, 10},
		MessageIndex:   99,
	}
	data, err := json.Marshal(orig)
	if err != nil {
		t.Fatalf("MarshalJSON: %v", err)
	}

	var loaded Location
	if err := json.Unmarshal(data, &loaded); err != nil {
		t.Fatalf("UnmarshalJSON: %v", err)
	}
	if loaded.FLasti != nil {
		t.Fatalf("f_lasti should be nil, got %v", *loaded.FLasti)
	}
}

func TestCursorJSONRoundTrip(t *testing.T) {
	fLasti := 30
	loc := Location{
		ThreadID:       1,
		FunctionCounts: []int{10, 20, 30},
		FLasti:         &fLasti,
		MessageIndex:   42,
	}
	orig := NewCursor(loc, nil, nil)
	data, err := json.Marshal(orig)
	if err != nil {
		t.Fatalf("MarshalJSON: %v", err)
	}

	var loaded Cursor
	if err := json.Unmarshal(data, &loaded); err != nil {
		t.Fatalf("UnmarshalJSON: %v", err)
	}
	got := loaded.Location()
	if got.MessageIndex != 42 {
		t.Fatalf("message index = %d, want 42", got.MessageIndex)
	}
	if got.ThreadID != 1 {
		t.Fatalf("thread_id = %d, want 1", got.ThreadID)
	}
	if len(got.FunctionCounts) != 3 || got.FunctionCounts[1] != 20 {
		t.Fatalf("function_counts mismatch: %v", got.FunctionCounts)
	}
	if got.FLasti == nil || *got.FLasti != 30 {
		t.Fatalf("f_lasti mismatch: %v", got.FLasti)
	}
}

func TestSnapshotProviderAt(t *testing.T) {
	oldStart := startReplayProcess
	defer func() { startReplayProcess = oldStart }()

	serverConn, clientConn := net.Pipe()
	startReplayProcess = func(target runnerTarget, stdout, stderr io.Writer) (*ControlClient, *os.Process, func(), error) {
		return NewControlClient(clientConn), nil, func() {}, nil
	}

	go mockForkingServer(serverConn, func(r *bufio.Reader, w *bufio.Writer) {
		_, _ = r.ReadBytes('\n')
		_ = json.NewEncoder(w).Encode(map[string]any{
			"kind": "stop",
			"payload": map[string]any{
				"reason":        "cursor",
				"message_index": 42,
				"cursor": map[string]any{
					"thread_id":       1,
					"function_counts": []int{1, 2},
					"f_lasti":         10,
				},
			},
		})
		_ = w.Flush()
	})

	pidFile := writeTempPidFile(t)
	ctx := context.Background()
	root, err := StartReplayFromPidFile(ctx, pidFile, nil, nil)
	if err != nil {
		t.Fatalf("StartReplayFromPidFile: %v", err)
	}
	defer root.Close()

	provider := NewSimpleSnapshotProvider(root)
	fLasti := 10
	loc := Location{
		ThreadID:       1,
		FunctionCounts: []int{1, 2},
		FLasti:         &fLasti,
		MessageIndex:   42,
	}

	snap, err := provider.ClosestBeforeCall(ctx, loc.ThreadID, loc.FunctionCounts)
	if err != nil {
		t.Fatalf("ClosestBeforeCall error: %v", err)
	}
	rp, err := snap.Replay(ctx)
	if err != nil {
		t.Fatalf("Replay error: %v", err)
	}
	if rp == nil {
		t.Fatal("expected non-nil replay")
	}
	defer rp.Close()
}

func TestCursorWithoutProviderReturnsNotImplemented(t *testing.T) {
	loc := Location{ThreadID: 1, FunctionCounts: []int{1, 2}, MessageIndex: 10}
	cur := NewCursor(loc, nil, nil)
	_, err := cur.Next(context.Background())
	if err == nil {
		t.Fatal("expected error, got nil")
	}
}

func TestDebuggerAddBreakpointPopulatesHitList(t *testing.T) {
	oldStart := startReplayProcess
	defer func() { startReplayProcess = oldStart }()

	var callCount int
	var mu sync.Mutex

	startReplayProcess = func(target runnerTarget, stdout, stderr io.Writer) (*ControlClient, *os.Process, func(), error) {
		mu.Lock()
		callCount++
		n := callCount
		mu.Unlock()

		serverConn, clientConn := net.Pipe()
		if n == 1 {
			go mockSimpleHelloServer(serverConn)
		} else {
			go mockBreakpointScanServer(serverConn)
		}
		return NewControlClient(clientConn), nil, func() {}, nil
	}

	pidFile := writeTempPidFile(t)
	ctx := context.Background()
	root, err := StartReplayFromPidFile(ctx, pidFile, nil, nil)
	if err != nil {
		t.Fatalf("StartReplayFromPidFile: %v", err)
	}
	engine := NewQueryEngine(root, pidFile, os.Stderr)
	debugger := NewDebugger(engine)
	defer debugger.Close()

	id, err := debugger.AddBreakpoint(ctx, BreakpointSpec{
		File: "/tmp/a.py", Line: 10,
	})
	if err != nil {
		t.Fatalf("AddBreakpoint: %v", err)
	}
	if id == 0 {
		t.Fatal("expected non-zero breakpoint ID")
	}

	for i := 0; i < 200; i++ {
		if debugger.Hits().Len() >= 2 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	hits := debugger.Hits()
	if hits.Len() != 2 {
		t.Fatalf("expected 2 hits, got %d", hits.Len())
	}
	if hits.At(0).Location.MessageIndex != 5 || hits.At(1).Location.MessageIndex != 15 {
		t.Fatalf("unexpected message indices: %d, %d",
			hits.At(0).Location.MessageIndex, hits.At(1).Location.MessageIndex)
	}
	if hits.At(0).BreakpointID != id {
		t.Fatalf("hit breakpoint ID = %d, want %d", hits.At(0).BreakpointID, id)
	}
	fc := hits.At(0).Location.FunctionCounts
	if len(fc) != 2 || fc[0] != 10 || fc[1] != 20 {
		t.Fatalf("unexpected function_counts: %v", fc)
	}
	if hits.At(0).Location.ThreadID != 1 {
		t.Fatalf("unexpected thread_id: %d", hits.At(0).Location.ThreadID)
	}
}

func mockSimpleHelloServer(conn net.Conn) {
	defer conn.Close()
	r := bufio.NewReader(conn)
	w := bufio.NewWriter(conn)
	line, _ := r.ReadBytes('\n')
	var req map[string]any
	_ = json.Unmarshal(line, &req)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"id": req["id"], "ok": true,
		"result": map[string]any{"protocol": "control", "version": 1},
	})
	_ = w.Flush()
	// Keep connection open until closed externally.
	_, _ = r.ReadBytes('\n')
}

func mockBreakpointScanServer(conn net.Conn) {
	defer conn.Close()
	r := bufio.NewReader(conn)
	w := bufio.NewWriter(conn)

	// hello
	line, _ := r.ReadBytes('\n')
	var req map[string]any
	_ = json.Unmarshal(line, &req)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"id": req["id"], "ok": true,
		"result": map[string]any{"protocol": "control", "version": 1},
	})
	_ = w.Flush()

	// hit_breakpoints command
	_, _ = r.ReadBytes('\n')
	_ = json.NewEncoder(w).Encode(map[string]any{
		"kind": "event", "event": "breakpoint_hit",
		"payload": map[string]any{
			"cursor": map[string]any{
				"thread_id":       1,
				"function_counts": []int{10, 20},
				"f_lasti":         100,
			},
			"message_index": 5,
		},
	})
	_ = w.Flush()
	_ = json.NewEncoder(w).Encode(map[string]any{
		"kind": "event", "event": "breakpoint_hit",
		"payload": map[string]any{
			"cursor": map[string]any{
				"thread_id":       1,
				"function_counts": []int{30, 40},
				"f_lasti":         200,
			},
			"message_index": 15,
		},
	})
	_ = w.Flush()
	_ = json.NewEncoder(w).Encode(map[string]any{"kind": "stop"})
	_ = w.Flush()
}
