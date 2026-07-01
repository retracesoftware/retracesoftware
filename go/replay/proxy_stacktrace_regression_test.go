package replay

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"strings"
	"testing"
	"time"
)

func TestProxyStackTraceDoesNotHideNotStoppedControlError(t *testing.T) {
	controlProxy, controlTarget := net.Pipe()
	defer controlProxy.Close()
	defer controlTarget.Close()

	controlDone := make(chan error, 1)
	go func() {
		defer close(controlDone)
		reader := bufio.NewReader(controlTarget)
		line, err := reader.ReadBytes('\n')
		if err != nil {
			controlDone <- err
			return
		}
		request, err := parseControlMessage(line)
		if err != nil {
			controlDone <- err
			return
		}
		if request.Method != "stack" {
			controlDone <- fmt.Errorf("control method = %q, want stack", request.Method)
			return
		}
		response := ControlResponse{
			ID: request.ID,
			OK: false,
			Error: &ControlError{
				Code:    "not_stopped",
				Message: "stopped-state inspection is unavailable",
			},
		}
		if err := json.NewEncoder(controlTarget).Encode(response); err != nil {
			controlDone <- err
		}
	}()

	clientToProxyR, clientToProxyW := io.Pipe()
	proxyToClientR, proxyToClientW := io.Pipe()
	proxy := NewProxy("recording.d/123.bin", clientToProxyR, NewWriter(proxyToClientW))
	proxy.currentCursor = NewCursor(
		Location{ThreadID: 1, FunctionCounts: []int{1}, MessageIndex: 123},
		nil,
		&Replay{client: NewControlClient(controlProxy)},
	)

	proxyDone := make(chan error, 1)
	go func() {
		proxyDone <- proxy.handlePostLaunch()
		_ = proxyToClientW.Close()
	}()

	dapRequest := map[string]any{
		"seq":       1,
		"type":      "request",
		"command":   "stackTrace",
		"arguments": map[string]any{"threadId": 1},
	}
	body, err := json.Marshal(dapRequest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fmt.Fprintf(clientToProxyW, "Content-Length: %d\r\n\r\n", len(body)); err != nil {
		t.Fatal(err)
	}
	if _, err := clientToProxyW.Write(body); err != nil {
		t.Fatal(err)
	}

	rawResponse, err := ReadMessage(bufio.NewReader(proxyToClientR))
	if err != nil {
		t.Fatal(err)
	}
	var response map[string]any
	if err := json.Unmarshal(rawResponse, &response); err != nil {
		t.Fatal(err)
	}

	disconnect := map[string]any{"seq": 2, "type": "request", "command": "disconnect"}
	disconnectBody, err := json.Marshal(disconnect)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = fmt.Fprintf(clientToProxyW, "Content-Length: %d\r\n\r\n", len(disconnectBody))
	_, _ = clientToProxyW.Write(disconnectBody)
	_, _ = ReadMessage(bufio.NewReader(proxyToClientR))
	_ = clientToProxyW.Close()

	select {
	case err := <-proxyDone:
		if err != nil {
			t.Fatalf("proxy exited with error: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("proxy did not exit after disconnect")
	}
	select {
	case err := <-controlDone:
		if err != nil {
			t.Fatalf("control server error: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("control server did not receive stack request")
	}

	if response["success"] != false {
		t.Fatalf("stackTrace success = %v, want false for not_stopped control error; response=%s", response["success"], rawResponse)
	}
	message, _ := response["message"].(string)
	if !strings.Contains(message, "not_stopped") {
		t.Fatalf("stackTrace message = %q, want not_stopped; response=%s", message, rawResponse)
	}
	respBody, _ := response["body"].(map[string]any)
	retrace, _ := respBody["retrace"].(map[string]any)
	if retrace["category"] != "inspection_unavailable" {
		t.Fatalf("stackTrace retrace.category = %v, want inspection_unavailable; response=%s", retrace["category"], rawResponse)
	}
	if retrace["code"] != "not_stopped" {
		t.Fatalf("stackTrace retrace.code = %v, want not_stopped; response=%s", retrace["code"], rawResponse)
	}
}
