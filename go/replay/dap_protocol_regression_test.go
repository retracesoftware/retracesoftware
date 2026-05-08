package replay

import (
	"bufio"
	"io"
	"testing"
	"time"
)

func readDAPInitializeCapabilities(t *testing.T) map[string]any {
	t.Helper()

	clientToProxyR, clientToProxyW := io.Pipe()
	proxyToClientR, proxyToClientW := io.Pipe()

	dapWriter := NewWriter(proxyToClientW)
	proxy := NewProxy("", clientToProxyR, dapWriter)

	proxyDone := make(chan error, 1)
	go func() {
		proxyDone <- proxy.Run()
		_ = proxyToClientW.Close()
	}()
	t.Cleanup(func() {
		_ = clientToProxyW.Close()
		_ = proxyToClientR.Close()
		select {
		case <-proxyDone:
		case <-time.After(2 * time.Second):
			t.Fatal("DAP proxy did not exit after initialize probe")
		}
	})

	client := &dapClient{r: bufio.NewReader(proxyToClientR), w: clientToProxyW}
	client.send("initialize", map[string]any{
		"clientID":  "test",
		"adapterID": "retrace",
	})

	msgs, ok := client.readUntilTimeout(isResponse("initialize"), 5*time.Second)
	if !ok {
		t.Fatalf("never got initialize response; messages: %v", msgs)
	}
	for _, msg := range msgs {
		if msg["type"] != "response" || msg["command"] != "initialize" {
			continue
		}
		body, _ := msg["body"].(map[string]any)
		return body
	}
	t.Fatalf("missing initialize response body: %v", msgs)
	return nil
}

func dapCapabilityEnabled(caps map[string]any, name string) bool {
	enabled, _ := caps[name].(bool)
	return enabled
}

func dapExceptionFiltersAdvertised(caps map[string]any) bool {
	filters, _ := caps["exceptionBreakpointFilters"].([]any)
	return len(filters) > 0
}

func readDAPResponse(t *testing.T, session *dapSession, command string, args map[string]any) map[string]any {
	t.Helper()
	session.client.send(command, args)
	msgs, ok := session.client.readUntilTimeout(isResponse(command), 5*time.Second)
	if !ok {
		t.Fatalf("never got %s response; messages: %v", command, msgs)
	}
	for _, msg := range msgs {
		if msg["type"] == "response" && msg["command"] == command {
			return msg
		}
	}
	t.Fatalf("missing %s response in messages: %v", command, msgs)
	return nil
}

func checkDAPResponseSucceeded(t *testing.T, command string, resp map[string]any) {
	t.Helper()
	if ok, _ := resp["success"].(bool); !ok {
		t.Errorf("%s failed even though initialize advertised support: %v", command, resp)
	}
}

func TestDAPAdvertisedRequestsAreImplemented(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}
	python := requirePython312(t)
	caps := readDAPInitializeCapabilities(t)
	session := newDAPSession(t, python, "advertised_requests_target.py", `value = 1
print(value)
`)

	if dapCapabilityEnabled(caps, "supportsFunctionBreakpoints") {
		resp := readDAPResponse(t, session, "setFunctionBreakpoints", map[string]any{
			"breakpoints": []map[string]any{{"name": "<module>"}},
		})
		checkDAPResponseSucceeded(t, "setFunctionBreakpoints", resp)
	}

	if dapExceptionFiltersAdvertised(caps) {
		resp := readDAPResponse(t, session, "setExceptionBreakpoints", map[string]any{
			"filters": []string{"uncaught"},
		})
		checkDAPResponseSucceeded(t, "setExceptionBreakpoints", resp)
	}

	if dapCapabilityEnabled(caps, "supportsGotoTargetsRequest") {
		resp := readDAPResponse(t, session, "gotoTargets", map[string]any{
			"source": map[string]any{
				"name": "advertised_requests_target.py",
				"path": session.script,
			},
			"line": 1,
		})
		checkDAPResponseSucceeded(t, "gotoTargets", resp)
	}

	if dapCapabilityEnabled(caps, "supportsExceptionInfoRequest") {
		session.setBreakpoint(1)
		session.configurationDone()
		session.continueToBreakpoint()
		resp := readDAPResponse(t, session, "exceptionInfo", map[string]any{"threadId": 1})
		checkDAPResponseSucceeded(t, "exceptionInfo", resp)
	}

	if dapCapabilityEnabled(caps, "supportsRestartRequest") {
		resp := readDAPResponse(t, session, "restart", nil)
		checkDAPResponseSucceeded(t, "restart", resp)
	}
}

func TestDAPNonExecutableBreakpointIsNotVerified(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}
	python := requirePython312(t)
	session := newDAPSession(t, python, "non_executable_breakpoint_target.py", `value = 1
print(value)
`)

	resp := readDAPResponse(t, session, "setBreakpoints", map[string]any{
		"source": map[string]any{
			"name": "non_executable_breakpoint_target.py",
			"path": session.script,
		},
		"breakpoints": []map[string]any{{"line": 999}},
	})

	body, _ := resp["body"].(map[string]any)
	breakpoints, _ := body["breakpoints"].([]any)
	if len(breakpoints) != 1 {
		t.Fatalf("expected exactly one breakpoint response, got: %v", resp)
	}
	first, _ := breakpoints[0].(map[string]any)
	if verified, _ := first["verified"].(bool); verified {
		t.Fatalf("non-existent breakpoint line was reported as verified: %v", resp)
	}
}

func TestDAPResponseSequenceNumbersAdvance(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}
	python := requirePython312(t)
	session := newDAPSession(t, python, "response_sequence_target.py", `value = 1
print(value)
`)

	threadsResp := readDAPResponse(t, session, "threads", nil)
	breakpointsResp := readDAPResponse(t, session, "setBreakpoints", map[string]any{
		"source": map[string]any{
			"name": "response_sequence_target.py",
			"path": session.script,
		},
		"breakpoints": []map[string]any{{"line": 1}},
	})

	threadsSeq, _ := threadsResp["seq"].(float64)
	breakpointsSeq, _ := breakpointsResp["seq"].(float64)
	if threadsSeq == 0 || breakpointsSeq == 0 {
		t.Fatalf("missing DAP sequence numbers: threads=%v setBreakpoints=%v", threadsResp, breakpointsResp)
	}
	if breakpointsSeq <= threadsSeq {
		t.Fatalf("DAP sequence numbers did not advance: threads seq=%v setBreakpoints seq=%v", threadsSeq, breakpointsSeq)
	}
}
