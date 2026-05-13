package replay

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"slices"
	"testing"
	"time"
)

func TestCursorStepIntoFollowsReplayInsteadOfSynthesizingCursor(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	serverConn, clientConn := net.Pipe()
	start := Location{
		ThreadID:     1,
		Coordinates:  Coordinates{3, 5},
		Lineno:       13,
		MessageIndex: 100,
	}
	rp := &Replay{
		client:   NewControlClient(clientConn),
		location: start,
		dead:     make(chan struct{}),
	}
	defer rp.Close()

	serverErr := make(chan error, 1)
	go func() {
		defer serverConn.Close()

		r := bufio.NewReader(serverConn)
		w := bufio.NewWriter(serverConn)
		var req ControlRequest
		line, err := r.ReadBytes('\n')
		if err != nil {
			serverErr <- err
			return
		}
		if err := json.Unmarshal(line, &req); err != nil {
			serverErr <- err
			return
		}
		if req.Method != "next_instruction" {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"id": req.ID,
				"error": map[string]any{
					"code":    "unexpected_method",
					"message": fmt.Sprintf("got %s, want next_instruction", req.Method),
				},
			})
			_ = w.Flush()
			serverErr <- fmt.Errorf("got method %q, want next_instruction", req.Method)
			return
		}
		if err := json.NewEncoder(w).Encode(map[string]any{
			"kind": "stop",
			"payload": map[string]any{
				"reason":        "step",
				"message_index": 101,
				"cursor": map[string]any{
					"thread_id":   1,
					"coordinates": []int{3, 5},
					"lineno":      17,
				},
			},
		}); err != nil {
			serverErr <- err
			return
		}
		if err := w.Flush(); err != nil {
			serverErr <- err
			return
		}
		serverErr <- nil
	}()

	cur := NewCursor(start, nil, rp)
	next, err := cur.StepInto(ctx)
	if err != nil {
		t.Fatalf("StepInto: %v", err)
	}
	if err := <-serverErr; err != nil {
		t.Fatal(err)
	}

	got := next.Location()
	if got.Lineno != 17 {
		t.Fatalf("lineno = %d, want 17", got.Lineno)
	}
	if got.MessageIndex != 101 {
		t.Fatalf("message index = %d, want 101", got.MessageIndex)
	}
	if !slices.Equal(got.Coordinates, Coordinates{3, 5}) {
		t.Fatalf("function counts = %v, want [3 5]", got.Coordinates)
	}
}

func TestRunToCursorReturnsErrorWhenReplayOvershoots(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	serverConn, clientConn := net.Pipe()
	rp := &Replay{
		client: NewControlClient(clientConn),
		dead:   make(chan struct{}),
	}
	defer rp.Close()

	serverErr := make(chan error, 1)
	go func() {
		defer serverConn.Close()

		r := bufio.NewReader(serverConn)
		w := bufio.NewWriter(serverConn)
		var req ControlRequest
		line, err := r.ReadBytes('\n')
		if err != nil {
			serverErr <- err
			return
		}
		if err := json.Unmarshal(line, &req); err != nil {
			serverErr <- err
			return
		}
		if req.Method != "run_to_cursor" {
			serverErr <- fmt.Errorf("got method %q, want run_to_cursor", req.Method)
			return
		}
		if err := json.NewEncoder(w).Encode(map[string]any{
			"kind": "stop",
			"payload": map[string]any{
				"reason":        "overshoot",
				"message_index": 12,
				"cursor":        map[string]any{},
			},
		}); err != nil {
			serverErr <- err
			return
		}
		if err := w.Flush(); err != nil {
			serverErr <- err
			return
		}
		serverErr <- nil
	}()

	_, err := rp.RunToCursor(ctx, RawCursor{
		ThreadID:    1,
		Coordinates: Coordinates{999, 0},
	})
	if err == nil {
		t.Fatal("expected overshoot error, got nil")
	}
	if err := <-serverErr; err != nil {
		t.Fatal(err)
	}
}
