package replay

import (
	"bufio"
	"context"
	"encoding/json"
	"net"
	"testing"
	"time"
)

func TestParseControlMessageKinds(t *testing.T) {
	cases := []struct {
		raw      string
		wantKind string
		wantType string
	}{
		{`{"id":"1","method":"hello","params":{}}`, "", ""},
		{`{"id":"1","ok":true,"result":{"x":1}}`, "", ""},
		{`{"id":"1","kind":"event","event":"breakpoint_hit","payload":{"cursor":{"thread_id":1,"function_counts":[1,2]}}}`, "event", ""},
		{`{"kind":"stop","payload":{"reason":"cursor","cursor":{"thread_id":1,"function_counts":[1,2]}}}`, "stop", ""},
		// backward compat: old-style messages with "type" still parse
		{`{"id":"1","type":"response","ok":true,"result":{"x":1}}`, "", "response"},
	}
	for _, tc := range cases {
		msg, err := parseControlMessage([]byte(tc.raw))
		if err != nil {
			t.Fatalf("parse failed for %q: %v", tc.raw, err)
		}
		if msg.Kind != tc.wantKind {
			t.Fatalf("kind=%q, want %q for %q", msg.Kind, tc.wantKind, tc.raw)
		}
		if msg.Type != tc.wantType {
			t.Fatalf("type=%q, want %q for %q", msg.Type, tc.wantType, tc.raw)
		}
	}
}

func TestControlClientRequestRoundTrip(t *testing.T) {
	serverConn, clientConn := net.Pipe()
	defer serverConn.Close()
	defer clientConn.Close()

	client := NewControlClient(clientConn)
	defer client.Close()

	done := make(chan struct{})
	go func() {
		defer close(done)
		reader := bufio.NewReader(serverConn)
		writer := bufio.NewWriter(serverConn)
		line, err := reader.ReadBytes('\n')
		if err != nil {
			return
		}
		var req ControlRequest
		if err := json.Unmarshal(line, &req); err != nil {
			return
		}
		resp := map[string]any{
			"id":     req.ID,
			"ok":     true,
			"result": map[string]any{"protocol_version": float64(1)},
		}
		b, _ := json.Marshal(resp)
		_, _ = writer.Write(b)
		_ = writer.WriteByte('\n')
		_ = writer.Flush()
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	resp, err := client.Request(ctx, "hello", nil)
	if err != nil {
		t.Fatalf("request failed: %v", err)
	}
	if !resp.OK {
		t.Fatalf("expected ok response: %#v", resp)
	}
	<-done
}

func TestControlClientReadsForkHelloEvent(t *testing.T) {
	serverConn, clientConn := net.Pipe()
	defer serverConn.Close()
	defer clientConn.Close()

	client := NewControlClient(clientConn)
	defer client.Close()

	go func() {
		writer := bufio.NewWriter(serverConn)
		_, _ = writer.WriteString(`{"type":"event","event":"fork_hello","payload":{"fork_id":"f-1","pid":123}}` + "\n")
		_ = writer.Flush()
	}()

	msg, err := client.ReadMessage()
	if err != nil {
		t.Fatalf("read message: %v", err)
	}
	if msg.Type != "event" || msg.Event != "fork_hello" {
		t.Fatalf("unexpected message: %#v", msg)
	}
	if msg.Payload["fork_id"] != "f-1" {
		t.Fatalf("unexpected payload: %#v", msg.Payload)
	}
}

func TestReplayFork(t *testing.T) {
	serverConn, clientConn := net.Pipe()
	defer serverConn.Close()

	ac, err := NewAwaitingCollection()
	if err != nil {
		t.Fatalf("NewAwaitingCollection: %v", err)
	}

	parent := &Replay{
		client:  NewControlClient(clientConn),
		cleanup: func() {},
		forks:   ac,
	}
	defer parent.Close()

	go func() {
		r := bufio.NewReader(serverConn)
		w := bufio.NewWriter(serverConn)

		line, _ := r.ReadBytes('\n')
		var req map[string]any
		_ = json.Unmarshal(line, &req)

		resp := map[string]any{
			"id": req["id"], "ok": true,
			"result": map[string]any{"pid": float64(99999)},
		}
		b, _ := json.Marshal(resp)
		_, _ = w.Write(b)
		_ = w.WriteByte('\n')
		_ = w.Flush()

		childConn, dialErr := net.Dial("unix", ac.SocketPath())
		if dialErr != nil {
			return
		}
		defer childConn.Close()
		cw := bufio.NewWriter(childConn)
		hello := map[string]any{
			"type": "event", "event": "fork_hello",
			"payload": map[string]any{"pid": float64(99999)},
		}
		hb, _ := json.Marshal(hello)
		_, _ = cw.Write(hb)
		_ = cw.WriteByte('\n')
		_ = cw.Flush()

		cr := bufio.NewReader(childConn)
		_, _ = cr.ReadBytes('\n')
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	child, err := parent.fork(ctx)
	if err != nil {
		t.Fatalf("fork: %v", err)
	}
	defer child.Close()

	if child.client == nil {
		t.Fatal("forked replay has nil client")
	}
	if child.forks != parent.forks {
		t.Fatal("child should share parent's AwaitingCollection")
	}
}

func TestParseStopResult(t *testing.T) {
	got := parseStopResult(map[string]any{
		"reason":        "breakpoint",
		"message_index": float64(12),
		"cursor": map[string]any{
			"thread_id":       float64(1),
			"function_counts": []any{float64(1), float64(2)},
			"f_lasti":         float64(42),
		},
		"thread_cursors": map[string]any{
			"1": map[string]any{
				"thread_id":       float64(1),
				"function_counts": []any{float64(7), float64(8)},
			},
		},
	})
	if got.Reason != "breakpoint" || got.MessageIndex != 12 {
		t.Fatalf("unexpected stop result: %#v", got)
	}
	if got.Cursor.ThreadID != 1 {
		t.Fatalf("unexpected cursor thread_id: %d", got.Cursor.ThreadID)
	}
	if len(got.Cursor.FunctionCounts) != 2 || got.Cursor.FunctionCounts[0] != 1 {
		t.Fatalf("unexpected cursor function_counts: %#v", got.Cursor.FunctionCounts)
	}
	if got.Cursor.FLasti == nil || *got.Cursor.FLasti != 42 {
		t.Fatalf("unexpected cursor f_lasti: %v", got.Cursor.FLasti)
	}
	tc := got.ThreadCursors[1]
	if len(tc.FunctionCounts) != 2 || tc.FunctionCounts[1] != 8 {
		t.Fatalf("unexpected thread cursors: %#v", got.ThreadCursors)
	}
}
