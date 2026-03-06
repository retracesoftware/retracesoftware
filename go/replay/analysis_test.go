package replay

import (
	"bufio"
	"encoding/json"
	"io"
	"net"
	"os"
	"path/filepath"
	"testing"
)

func mockReplayServer(serverConn net.Conn, handler func(r *bufio.Reader, w *bufio.Writer)) {
	defer serverConn.Close()
	r := bufio.NewReader(serverConn)
	w := bufio.NewWriter(serverConn)

	// hello handshake (consumed by StartReplay)
	line, _ := r.ReadBytes('\n')
	var req map[string]any
	_ = json.Unmarshal(line, &req)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"id": req["id"], "ok": true,
		"result": map[string]any{"protocol": "control", "version": 1},
	})
	_ = w.Flush()

	handler(r, w)
}

// mockForkingServer handles the hello handshake, then sits in a loop
// handling fork requests. For each fork it connects a mock child to
// the AwaitingCollection socket (read from the fork request params)
// and runs childHandler on the child connection.
func mockForkingServer(serverConn net.Conn, childHandler func(r *bufio.Reader, w *bufio.Writer)) {
	defer serverConn.Close()
	r := bufio.NewReader(serverConn)
	w := bufio.NewWriter(serverConn)

	line, _ := r.ReadBytes('\n')
	var req map[string]any
	_ = json.Unmarshal(line, &req)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"id": req["id"], "ok": true,
		"result": map[string]any{"protocol": "control", "version": 1},
	})
	_ = w.Flush()

	childPID := 90000
	for {
		line, err := r.ReadBytes('\n')
		if err != nil {
			return
		}
		var forkReq map[string]any
		_ = json.Unmarshal(line, &forkReq)

		params, _ := forkReq["params"].(map[string]any)
		socketPath, _ := params["socket_path"].(string)

		pid := childPID
		childPID++

		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": forkReq["id"], "ok": true,
			"result": map[string]any{"pid": float64(pid)},
		})
		_ = w.Flush()

		go func(p int, sp string) {
			childConn, err := net.Dial("unix", sp)
			if err != nil {
				return
			}
			defer childConn.Close()
			cw := bufio.NewWriter(childConn)
			_ = json.NewEncoder(cw).Encode(map[string]any{
				"type": "event", "event": "fork_hello",
				"payload": map[string]any{"pid": float64(p)},
			})
			_ = cw.Flush()

			childHandler(bufio.NewReader(childConn), cw)
		}(pid, socketPath)
	}
}

func TestRunAnalysisProbeBreakpointHit(t *testing.T) {
	oldProbe := runAnalysisProbe
	defer func() { runAnalysisProbe = oldProbe }()

	oldStart := startReplayProcess
	defer func() { startReplayProcess = oldStart }()

	serverConn, clientConn := net.Pipe()
	startReplayProcess = func(target runnerTarget, stdout, stderr io.Writer) (*ControlClient, *os.Process, func(), error) {
		return NewControlClient(clientConn), nil, func() {}, nil
	}

	runAnalysisProbe = runAnalysisProbeCommand

	go mockReplayServer(serverConn, func(r *bufio.Reader, w *bufio.Writer) {
		_, _ = r.ReadBytes('\n')

		_ = json.NewEncoder(w).Encode(map[string]any{
			"kind": "event", "event": "breakpoint_hit",
			"payload": map[string]any{
				"cursor": map[string]any{
					"thread_id":       1,
					"function_counts": []int{100, 200},
					"f_lasti":         50,
				},
			},
		})
		_ = w.Flush()
	})

	pidFile := writeTempPidFile(t)
	result, err := runAnalysisProbeCommand(pidFile)
	if err != nil {
		t.Fatalf("runAnalysisProbeCommand: %v", err)
	}
	if result.SingleThreaded {
		t.Fatal("expected multi-threaded")
	}
	if result.LastCheckpointCursor == nil {
		t.Fatal("expected non-nil cursor")
	}
	if len(result.LastCheckpointCursor.FunctionCounts) != 2 || result.LastCheckpointCursor.FunctionCounts[0] != 100 {
		t.Fatalf("unexpected cursor function_counts: %v", result.LastCheckpointCursor.FunctionCounts)
	}
}

func TestRunAnalysisProbeEOFStop(t *testing.T) {
	oldStart := startReplayProcess
	defer func() { startReplayProcess = oldStart }()

	serverConn, clientConn := net.Pipe()
	startReplayProcess = func(target runnerTarget, stdout, stderr io.Writer) (*ControlClient, *os.Process, func(), error) {
		return NewControlClient(clientConn), nil, func() {}, nil
	}

	go mockReplayServer(serverConn, func(r *bufio.Reader, w *bufio.Writer) {
		_, _ = r.ReadBytes('\n')

		_ = json.NewEncoder(w).Encode(map[string]any{
			"kind": "stop",
			"payload": map[string]any{
				"reason":        "eof",
				"message_index": 500,
				"cursor":        map[string]any{},
				"thread_cursors": map[string]any{},
			},
		})
		_ = w.Flush()
	})

	pidFile := writeTempPidFile(t)
	result, err := runAnalysisProbeCommand(pidFile)
	if err != nil {
		t.Fatalf("runAnalysisProbeCommand: %v", err)
	}
	if !result.SingleThreaded {
		t.Fatal("expected single-threaded")
	}
}

func TestAnalysisCacheRoundTrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "analysis.json")

	rc := RawCursor{
		ThreadID:       1,
		FunctionCounts: []int{10, 20, 30},
		FLasti:         intPtr(50),
	}
	original := &TraceAnalysis{
		LastCheckpointCursor: &rc,
		SingleThreaded:       false,
	}

	if err := writeAnalysis(original, path); err != nil {
		t.Fatalf("writeAnalysis: %v", err)
	}

	loaded, err := loadAnalysis(path)
	if err != nil {
		t.Fatalf("loadAnalysis: %v", err)
	}

	if loaded.SingleThreaded != original.SingleThreaded {
		t.Fatalf("SingleThreaded = %v, want %v", loaded.SingleThreaded, original.SingleThreaded)
	}
	if loaded.LastCheckpointCursor == nil {
		t.Fatal("expected non-nil cursor")
	}
	if len(loaded.LastCheckpointCursor.FunctionCounts) != 3 || loaded.LastCheckpointCursor.FunctionCounts[1] != 20 {
		t.Fatalf("cursor mismatch: %v", loaded.LastCheckpointCursor.FunctionCounts)
	}
}

func TestAnalysisCacheSingleThreaded(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "analysis.json")

	original := &TraceAnalysis{SingleThreaded: true}

	if err := writeAnalysis(original, path); err != nil {
		t.Fatalf("writeAnalysis: %v", err)
	}
	loaded, err := loadAnalysis(path)
	if err != nil {
		t.Fatalf("loadAnalysis: %v", err)
	}
	if !loaded.SingleThreaded {
		t.Fatal("expected single-threaded after roundtrip")
	}
	if loaded.LastCheckpointCursor != nil {
		t.Fatalf("expected nil cursor, got %v", loaded.LastCheckpointCursor)
	}
}

func intPtr(v int) *int {
	return &v
}
