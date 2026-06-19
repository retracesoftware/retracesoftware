package replay

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// extractPidFile records a script and extracts a PidFile for the root PID.
// Returns the PidFile path and a cleanup function.
func extractPidFile(t *testing.T, python, script, tracePath string) (string, func()) {
	t.Helper()
	recordTrace(t, python, script, tracePath)

	pid, err := FirstPID(tracePath)
	if err != nil {
		t.Fatalf("FirstPID: %v", err)
	}

	_, tmpDir, err := ResolveProcess(tracePath, pid)
	if err != nil {
		t.Fatalf("ResolveProcess: %v", err)
	}

	pidFile := filepath.Join(tmpDir, fmt.Sprintf("%d.bin", pid))
	if _, err := os.Stat(pidFile); err != nil {
		os.RemoveAll(tmpDir)
		t.Fatalf("PidFile missing: %v", err)
	}
	return pidFile, func() { os.RemoveAll(tmpDir) }
}

// dapClient wraps a pipe pair for sending/receiving DAP messages.
type dapClient struct {
	r   *bufio.Reader
	w   io.Writer
	seq int
}

func (c *dapClient) send(command string, args any) {
	c.seq++
	msg := map[string]any{
		"seq":     c.seq,
		"type":    "request",
		"command": command,
	}
	if args != nil {
		raw, _ := json.Marshal(args)
		msg["arguments"] = json.RawMessage(raw)
	}
	body, _ := json.Marshal(msg)
	header := fmt.Sprintf("Content-Length: %d\r\n\r\n", len(body))
	_, _ = io.WriteString(c.w, header)
	_, _ = c.w.Write(body)
}

func (c *dapClient) read() map[string]any {
	raw, err := ReadMessage(c.r)
	if err != nil {
		return nil
	}
	var m map[string]any
	_ = json.Unmarshal(raw, &m)
	return m
}

// readUntil reads DAP messages until pred returns true or limit is reached.
// Returns all messages read and whether pred was satisfied.
func (c *dapClient) readUntil(pred func(map[string]any) bool, limit int) ([]map[string]any, bool) {
	var msgs []map[string]any
	for i := 0; i < limit; i++ {
		m := c.read()
		if m == nil {
			return msgs, false
		}
		msgs = append(msgs, m)
		if pred(m) {
			return msgs, true
		}
	}
	return msgs, false
}

func (c *dapClient) readUntilTimeout(pred func(map[string]any) bool, timeout time.Duration) ([]map[string]any, bool) {
	var msgs []map[string]any
	deadline := time.After(timeout)
	for {
		done := make(chan map[string]any, 1)
		go func() {
			done <- c.read()
		}()

		select {
		case m := <-done:
			if m == nil {
				return msgs, false
			}
			msgs = append(msgs, m)
			if pred(m) {
				return msgs, true
			}
		case <-deadline:
			return msgs, false
		}
	}
}

func isResponse(command string) func(map[string]any) bool {
	return func(m map[string]any) bool {
		return m["type"] == "response" && m["command"] == command
	}
}

func isEvent(event string) func(map[string]any) bool {
	return func(m map[string]any) bool {
		return m["type"] == "event" && m["event"] == event
	}
}

// findPython312 searches for a Python >=3.12 with retracesoftware installed.
// Checks checkout-local venvs, an optional override, and PATH.
func findPython312() (string, error) {
	var candidates []string
	if p := os.Getenv("RETRACE_TEST_PYTHON312"); p != "" {
		candidates = append(candidates, p)
	}
	if src := checkoutSrcDir(); src != "" {
		root := filepath.Dir(src)
		candidates = append(candidates,
			filepath.Join(root, ".venv312", "bin", "python"),
			filepath.Join(root, ".venv", "bin", "python"),
		)
	}
	for _, name := range []string{"python3.12", "python3"} {
		if p, err := exec.LookPath(name); err == nil {
			candidates = append(candidates, p)
		}
	}

	for _, p := range candidates {
		if _, err := os.Stat(p); err != nil {
			continue
		}
		out, err := pythonCommand(p, "-c",
			"import sys; v=sys.version_info; print(f'{v.major}.{v.minor}'); "+pythonImportProbe).CombinedOutput()
		if err != nil {
			continue
		}
		ver := strings.TrimSpace(strings.SplitN(string(out), "\n", 2)[0])
		var major, minor int
		fmt.Sscanf(ver, "%d.%d", &major, &minor)
		if major >= 3 && minor >= 12 {
			return p, nil
		}
	}
	return "", fmt.Errorf("no Python >=3.12 with retracesoftware found")
}

// requirePython312 returns a Python >=3.12 path with retracesoftware, or skips.
// sys.monitoring (used by the breakpoint system) requires Python 3.12+.
func requirePython312(t *testing.T) string {
	t.Helper()
	p, err := findPython312()
	if err != nil {
		t.Skipf("skipping: %v", err)
	}
	return p
}

// findPython311 searches for a Python 3.11 with retracesoftware installed.
func findPython311() (string, error) {
	var candidates []string
	if p := os.Getenv("RETRACE_TEST_PYTHON311"); p != "" {
		candidates = append(candidates, p)
	}
	if src := checkoutSrcDir(); src != "" {
		root := filepath.Dir(src)
		candidates = append(candidates,
			filepath.Join(root, ".venv311", "bin", "python"),
		)
	}
	if p, err := exec.LookPath("python3.11"); err == nil {
		candidates = append(candidates, p)
	}

	for _, p := range candidates {
		if _, err := os.Stat(p); err != nil {
			continue
		}
		out, err := pythonCommand(p, "-c",
			"import sys; v=sys.version_info; print(f'{v.major}.{v.minor}'); "+pythonImportProbe).CombinedOutput()
		if err != nil {
			continue
		}
		ver := strings.TrimSpace(strings.SplitN(string(out), "\n", 2)[0])
		var major, minor int
		fmt.Sscanf(ver, "%d.%d", &major, &minor)
		if major == 3 && minor == 11 {
			return p, nil
		}
	}
	return "", fmt.Errorf("no Python 3.11 with retracesoftware found")
}

func requirePython311(t *testing.T) string {
	t.Helper()
	p, err := findPython311()
	if err != nil {
		t.Skipf("skipping: %v", err)
	}
	return p
}

type dapSession struct {
	t              *testing.T
	client         *dapClient
	clientToProxyW *io.PipeWriter
	proxyToClientR *io.PipeReader
	proxyDone      chan error
	cleanup        func()
	script         string
	closed         bool
}

func newDAPSession(t *testing.T, python, scriptName, source string) *dapSession {
	t.Helper()
	return newDAPSessionInScriptDir(t, python, filepath.Join(t.TempDir(), "src"), scriptName, source)
}

func newDAPSessionInScriptDir(t *testing.T, python, scriptDir, scriptName, source string) *dapSession {
	t.Helper()
	if err := os.MkdirAll(scriptDir, 0755); err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(scriptDir, scriptName)
	if err := os.WriteFile(script, []byte(source), 0644); err != nil {
		t.Fatal(err)
	}

	tmpDir := t.TempDir()
	tracePath := filepath.Join(tmpDir, "trace.retrace")
	pidFile, cleanup := extractPidFile(t, python, script, tracePath)

	clientToProxyR, clientToProxyW := io.Pipe()
	proxyToClientR, proxyToClientW := io.Pipe()

	dapWriter := NewWriter(proxyToClientW)
	proxy := NewProxy(pidFile, clientToProxyR, dapWriter)
	proxy.navTimeout = 5 * time.Second

	proxyDone := make(chan error, 1)
	go func() {
		proxyDone <- proxy.Run()
		proxyToClientW.Close()
	}()

	session := &dapSession{
		t:              t,
		client:         &dapClient{r: bufio.NewReader(proxyToClientR), w: clientToProxyW},
		clientToProxyW: clientToProxyW,
		proxyToClientR: proxyToClientR,
		proxyDone:      proxyDone,
		cleanup:        cleanup,
		script:         script,
	}
	t.Cleanup(session.close)

	session.initialize()
	session.launch(pidFile)
	return session
}

func (s *dapSession) close() {
	if s.closed {
		return
	}
	s.closed = true

	s.client.send("disconnect", nil)
	s.client.readUntilTimeout(isResponse("disconnect"), 500*time.Millisecond)
	_ = s.clientToProxyW.Close()
	_ = s.proxyToClientR.Close()

	select {
	case err := <-s.proxyDone:
		if err != nil {
			s.t.Logf("proxy exited with: %v (may be expected)", err)
		}
	case <-time.After(5 * time.Second):
		s.t.Fatal("proxy didn't shut down in time")
	}
	if s.cleanup != nil {
		s.cleanup()
	}
}

func (s *dapSession) initialize() {
	s.t.Helper()
	s.client.send("initialize", map[string]any{
		"clientID":  "test",
		"adapterID": "retrace",
	})
	msgs, ok := s.client.readUntilTimeout(isResponse("initialize"), 5*time.Second)
	if !ok {
		s.t.Fatalf("never got initialize response; messages: %v", msgs)
	}
	hasInitialized := false
	for _, m := range msgs {
		if m["type"] == "event" && m["event"] == "initialized" {
			hasInitialized = true
		}
	}
	if !hasInitialized {
		msgs2, ok2 := s.client.readUntilTimeout(isEvent("initialized"), 5*time.Second)
		if !ok2 {
			s.t.Fatalf("never got initialized event; messages: %v", append(msgs, msgs2...))
		}
	}
}

func (s *dapSession) launch(pidFile string) {
	s.t.Helper()
	s.client.send("launch", map[string]any{
		"type":      "retrace",
		"request":   "launch",
		"recording": pidFile,
	})
	s.expectResponse("launch", 10*time.Second)
}

func (s *dapSession) setBreakpoint(line int) {
	s.t.Helper()
	s.setBreakpoints([]int{line})
}

func (s *dapSession) setBreakpoints(lines []int) {
	s.t.Helper()
	requestBreakpoints := make([]map[string]any, 0, len(lines))
	for _, line := range lines {
		requestBreakpoints = append(requestBreakpoints, map[string]any{"line": line})
	}
	s.client.send("setBreakpoints", map[string]any{
		"source": map[string]any{
			"name": filepath.Base(s.script),
			"path": s.script,
		},
		"lines":       lines,
		"breakpoints": requestBreakpoints,
	})
	resp := s.expectResponse("setBreakpoints", 15*time.Second)
	body, _ := resp["body"].(map[string]any)
	responseBreakpoints, _ := body["breakpoints"].([]any)
	if len(responseBreakpoints) == 0 {
		s.t.Fatalf("setBreakpoints returned no breakpoints: %v", resp)
	}
	first, _ := responseBreakpoints[0].(map[string]any)
	if first["verified"] != true {
		s.t.Fatalf("breakpoint was not verified: %v", resp)
	}
}

func (s *dapSession) configurationDone() {
	s.t.Helper()
	s.client.send("configurationDone", nil)
	s.expectStopped("entry", 10*time.Second)
}

func (s *dapSession) continueToBreakpoint() {
	s.t.Helper()
	s.client.send("continue", map[string]any{"threadId": 1})
	s.expectStopped("breakpoint", 30*time.Second)
}

func (s *dapSession) step(command string) {
	s.t.Helper()
	s.client.send(command, map[string]any{"threadId": 1})
	s.expectStopped("step", 15*time.Second)
}

func (s *dapSession) expectResponse(command string, timeout time.Duration) map[string]any {
	s.t.Helper()
	msgs, ok := s.client.readUntilTimeout(isResponse(command), timeout)
	if !ok {
		s.t.Fatalf("never got %s response; messages: %v", command, msgs)
	}
	for _, m := range msgs {
		if m["type"] == "response" && m["command"] == command {
			if m["success"] != true {
				s.t.Fatalf("%s failed: %v", command, m)
			}
			return m
		}
	}
	s.t.Fatalf("missing %s response in messages: %v", command, msgs)
	return nil
}

func (s *dapSession) expectStopped(reason string, timeout time.Duration) map[string]any {
	s.t.Helper()
	msgs, ok := s.client.readUntilTimeout(func(m map[string]any) bool {
		if m["type"] != "event" {
			return false
		}
		return m["event"] == "stopped" || m["event"] == "terminated"
	}, timeout)
	if !ok {
		s.t.Fatalf("never got stopped/terminated event; messages: %v", msgs)
	}
	for _, m := range msgs {
		if m["type"] != "event" {
			continue
		}
		switch m["event"] {
		case "terminated":
			s.t.Fatalf("session terminated while waiting for stopped(%s); messages:\n%v", reason, formatMsgs(msgs))
		case "stopped":
			body, _ := m["body"].(map[string]any)
			if body["reason"] != reason {
				s.t.Fatalf("stopped with reason %v, want %s; messages:\n%v", body["reason"], reason, formatMsgs(msgs))
			}
			return body
		}
	}
	s.t.Fatalf("missing stopped event in messages: %v", msgs)
	return nil
}

func (s *dapSession) topFrame() map[string]any {
	s.t.Helper()
	s.client.send("stackTrace", map[string]any{"threadId": 1})
	resp := s.expectResponse("stackTrace", 10*time.Second)
	body, _ := resp["body"].(map[string]any)
	frames, _ := body["stackFrames"].([]any)
	if len(frames) == 0 {
		s.t.Fatalf("stackTrace returned no frames: %v", resp)
	}
	topFrame, _ := frames[0].(map[string]any)
	return topFrame
}

func assertTopFrame(t *testing.T, frame map[string]any, script string, line int, function string) {
	t.Helper()
	source, _ := frame["source"].(map[string]any)
	sourcePath, _ := source["path"].(string)
	if sourcePath != script {
		t.Fatalf("top frame source path = %q, want %q", sourcePath, script)
	}
	if !fileExists(sourcePath) {
		t.Fatalf("top frame source path does not exist: %s", sourcePath)
	}
	gotLine, _ := frame["line"].(float64)
	if int(gotLine) != line {
		t.Fatalf("top frame line = %v, want %d; frame: %v", frame["line"], line, frame)
	}
	if function != "" && frame["name"] != function {
		t.Fatalf("top frame function = %v, want %s; frame: %v", frame["name"], function, frame)
	}
}

type dapFramePoint struct {
	Line     int
	Function string
}

func topFramePoint(t *testing.T, session *dapSession) dapFramePoint {
	t.Helper()
	frame := session.topFrame()
	source, _ := frame["source"].(map[string]any)
	sourcePath, _ := source["path"].(string)
	if sourcePath != session.script {
		t.Fatalf("top frame source path = %q, want %q", sourcePath, session.script)
	}
	if !fileExists(sourcePath) {
		t.Fatalf("top frame source path does not exist: %s", sourcePath)
	}
	line, _ := frame["line"].(float64)
	function, _ := frame["name"].(string)
	return dapFramePoint{Line: int(line), Function: function}
}

func readExampleTargetHello(t *testing.T) string {
	t.Helper()
	src := checkoutSrcDir()
	if src == "" {
		t.Fatal("could not locate checkout src dir")
	}
	root := filepath.Dir(src)
	data, err := os.ReadFile(filepath.Join(root, "examples", "target_hello.py"))
	if err != nil {
		t.Fatalf("read examples/target_hello.py: %v", err)
	}
	return string(data)
}

// TestDAPBreakpointE2E records a Python script, starts a Proxy, drives the
// full DAP flow (initialize → launch → setBreakpoints → configurationDone →
// continue), and verifies that the breakpoint is hit (stopped event with
// reason "breakpoint") rather than the session terminating.
//
// Requires Python >=3.12 (sys.monitoring).
func TestDAPBreakpointE2E(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}
	python := requirePython312(t)
	tmpDir := t.TempDir()

	// Use a stable path that persists through replay (not cleaned up mid-replay).
	scriptDir := filepath.Join(tmpDir, "src")
	if err := os.MkdirAll(scriptDir, 0755); err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(scriptDir, "bp_target.py")
	if err := os.WriteFile(script, []byte("x = 1\ny = 2\nz = x + y\nprint(z)\n"), 0644); err != nil {
		t.Fatal(err)
	}
	t.Logf("script path: %s (exists: %v)", script, fileExists(script))

	tracePath := filepath.Join(tmpDir, "trace.retrace")
	pidFile, cleanup := extractPidFile(t, python, script, tracePath)
	defer cleanup()

	t.Logf("pidFile: %s", pidFile)

	clientToProxyR, clientToProxyW := io.Pipe()
	proxyToClientR, proxyToClientW := io.Pipe()
	defer clientToProxyW.Close()

	dapWriter := NewWriter(proxyToClientW)
	proxy := NewProxy(pidFile, clientToProxyR, dapWriter)
	proxy.navTimeout = 5 * time.Second

	proxyDone := make(chan error, 1)
	go func() {
		proxyDone <- proxy.Run()
		proxyToClientW.Close()
	}()

	client := &dapClient{
		r: bufio.NewReader(proxyToClientR),
		w: clientToProxyW,
	}

	// 1. Initialize
	client.send("initialize", map[string]any{
		"clientID":  "test",
		"adapterID": "retrace",
	})
	msgs, ok := client.readUntil(isResponse("initialize"), 5)
	if !ok {
		t.Fatalf("never got initialize response; messages: %v", msgs)
	}
	// Also expect an 'initialized' event (may arrive before or after the response)
	hasInitialized := false
	for _, m := range msgs {
		if m["type"] == "event" && m["event"] == "initialized" {
			hasInitialized = true
		}
	}
	if !hasInitialized {
		msgs2, ok2 := client.readUntil(isEvent("initialized"), 5)
		if !ok2 {
			t.Fatalf("never got initialized event; messages: %v", append(msgs, msgs2...))
		}
	}
	t.Log("OK: initialize + initialized")

	// 2. Launch
	client.send("launch", map[string]any{
		"type":      "retrace",
		"request":   "launch",
		"recording": pidFile,
	})
	msgs, ok = client.readUntil(isResponse("launch"), 10)
	if !ok {
		t.Fatalf("never got launch response; messages: %v", msgs)
	}
	for _, m := range msgs {
		if m["type"] == "response" && m["command"] == "launch" {
			if m["success"] != true {
				t.Fatalf("launch failed: %v", m)
			}
		}
	}
	t.Log("OK: launch")

	// 3. setBreakpoints on line 1 of the script (x = 1)
	client.send("setBreakpoints", map[string]any{
		"source": map[string]any{
			"name": filepath.Base(script),
			"path": script,
		},
		"lines":       []int{1},
		"breakpoints": []map[string]any{{"line": 1}},
	})
	msgs, ok = client.readUntil(isResponse("setBreakpoints"), 10)
	if !ok {
		t.Fatalf("never got setBreakpoints response; messages: %v", msgs)
	}
	for _, m := range msgs {
		if m["type"] == "response" && m["command"] == "setBreakpoints" {
			if m["success"] != true {
				t.Fatalf("setBreakpoints failed: %v", m)
			}
		}
	}
	t.Log("OK: setBreakpoints")

	// 4. configurationDone → expect stopped event (reason: entry)
	client.send("configurationDone", nil)
	msgs, ok = client.readUntil(isEvent("stopped"), 10)
	if !ok {
		t.Fatalf("never got stopped event after configurationDone; messages: %v", msgs)
	}
	t.Log("OK: configurationDone → stopped(entry)")

	// 5. continue → expect stopped event (reason: breakpoint) NOT terminated
	t.Logf("script exists before continue: %v", fileExists(script))
	client.send("continue", map[string]any{"threadId": 1})

	// Read messages looking for either stopped or terminated
	var allMsgs []map[string]any
	gotBreakpoint := false
	gotTerminated := false
	deadline := time.After(30 * time.Second)

	for !gotBreakpoint && !gotTerminated {
		done := make(chan map[string]any, 1)
		go func() {
			done <- client.read()
		}()

		select {
		case m := <-done:
			if m == nil {
				t.Fatal("unexpected EOF reading DAP messages")
			}
			allMsgs = append(allMsgs, m)
			t.Logf("  DAP msg: type=%v event=%v command=%v", m["type"], m["event"], m["command"])

			if m["type"] == "event" {
				switch m["event"] {
				case "stopped":
					body, _ := m["body"].(map[string]any)
					if body["reason"] == "breakpoint" {
						gotBreakpoint = true
					}
				case "terminated":
					gotTerminated = true
				}
			}
		case <-deadline:
			t.Fatalf("timeout waiting for stopped/terminated; messages so far: %v", allMsgs)
		}
	}

	if gotTerminated {
		t.Fatalf("session terminated instead of hitting breakpoint; all messages:\n%v", formatMsgs(allMsgs))
	}
	if !gotBreakpoint {
		t.Fatalf("did not get breakpoint stopped event; all messages:\n%v", formatMsgs(allMsgs))
	}
	t.Log("OK: continue → stopped(breakpoint)")

	// 6. stackTrace → top frame should point at an openable source path
	client.send("stackTrace", map[string]any{"threadId": 1})
	msgs, ok = client.readUntil(isResponse("stackTrace"), 10)
	if !ok {
		t.Fatalf("never got stackTrace response; messages: %v", msgs)
	}
	var stackResp map[string]any
	for _, m := range msgs {
		if m["type"] == "response" && m["command"] == "stackTrace" {
			stackResp = m
			break
		}
	}
	if stackResp == nil || stackResp["success"] != true {
		t.Fatalf("stackTrace failed: %v", stackResp)
	}
	body, _ := stackResp["body"].(map[string]any)
	frames, _ := body["stackFrames"].([]any)
	if len(frames) == 0 {
		t.Fatalf("stackTrace returned no frames: %v", stackResp)
	}
	topFrame, _ := frames[0].(map[string]any)
	source, _ := topFrame["source"].(map[string]any)
	sourcePath, _ := source["path"].(string)
	if sourcePath != script {
		t.Fatalf("top frame source path = %q, want %q", sourcePath, script)
	}
	if !fileExists(sourcePath) {
		t.Fatalf("top frame source path does not exist: %s", sourcePath)
	}
	t.Log("OK: stackTrace source path")

	// 7. next / step-over -> expect a step stop on the next source line
	client.send("next", map[string]any{"threadId": 1})
	msgs, ok = client.readUntilTimeout(func(m map[string]any) bool {
		if m["type"] != "event" {
			return false
		}
		return m["event"] == "stopped" || m["event"] == "terminated"
	}, 10*time.Second)
	if !ok {
		t.Fatalf("never got stopped/terminated after next; messages: %v", msgs)
	}
	for _, m := range msgs {
		if m["type"] != "event" {
			continue
		}
		if m["event"] == "terminated" {
			t.Fatalf("session terminated during next; messages:\n%v", formatMsgs(msgs))
		}
		if m["event"] == "stopped" {
			body, _ := m["body"].(map[string]any)
			if body["reason"] != "step" {
				t.Fatalf("next stopped with reason %v; messages:\n%v", body["reason"], formatMsgs(msgs))
			}
		}
	}

	client.send("stackTrace", map[string]any{"threadId": 1})
	msgs, ok = client.readUntil(isResponse("stackTrace"), 10)
	if !ok {
		t.Fatalf("never got stackTrace response after next; messages: %v", msgs)
	}
	stackResp = nil
	for _, m := range msgs {
		if m["type"] == "response" && m["command"] == "stackTrace" {
			stackResp = m
			break
		}
	}
	if stackResp == nil || stackResp["success"] != true {
		t.Fatalf("stackTrace after next failed: %v", stackResp)
	}
	body, _ = stackResp["body"].(map[string]any)
	frames, _ = body["stackFrames"].([]any)
	if len(frames) == 0 {
		t.Fatalf("stackTrace after next returned no frames: %v", stackResp)
	}
	topFrame, _ = frames[0].(map[string]any)
	gotLine, _ := topFrame["line"].(float64)
	if int(gotLine) != 2 {
		t.Fatalf("top frame line after next = %v, want 2; response: %v", topFrame["line"], stackResp)
	}
	t.Log("OK: next → stopped(step) on line 2")

	// 8. disconnect
	client.send("disconnect", nil)
	client.readUntil(isResponse("disconnect"), 5)
	clientToProxyW.Close()

	select {
	case err := <-proxyDone:
		if err != nil {
			t.Logf("proxy exited with: %v (may be expected)", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("proxy didn't shut down in time")
	}
	t.Log("OK: disconnect")
}

// TestDAPDebuggerControlsE2E drives the common VS Code stepping requests
// against a real recording. It covers step-over (next), reverse step-over
// (stepBack), step-into, and step-out through the Go DAP proxy.
func TestDAPDebuggerControlsE2E(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}
	python := requirePython312(t)

	const source = `def add(a, b):
    c = a + b
    return c

value = 1
other = 2
total = add(value, other)
after = total + 1
print(after)
`

	session := newDAPSession(t, python, "controls_target.py", source)
	session.setBreakpoint(7)
	session.configurationDone()
	session.continueToBreakpoint()
	assertTopFrame(t, session.topFrame(), session.script, 7, "<module>")
	t.Log("OK: continue -> line 7")

	session.step("next")
	assertTopFrame(t, session.topFrame(), session.script, 8, "<module>")
	t.Log("OK: next -> line 8")

	session.step("stepBack")
	assertTopFrame(t, session.topFrame(), session.script, 7, "<module>")
	t.Log("OK: stepBack -> line 7")

	session.step("stepIn")
	assertTopFrame(t, session.topFrame(), session.script, 2, "add")
	t.Log("OK: stepIn -> add line 2")

	session.step("stepOut")
	assertTopFrame(t, session.topFrame(), session.script, 7, "<module>")
	t.Log("OK: stepOut -> caller line 7")
}

// TestDAPDebuggerControlsPython311E2E verifies that the Go DAP proxy's source
// breakpoint path works on Python 3.11, where the Python control runtime must
// use sys.settrace instead of sys.monitoring.
func TestDAPDebuggerControlsPython311E2E(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}
	python := requirePython311(t)

	const source = `def add(a, b):
    c = a + b
    return c

value = 1
other = 2
total = add(value, other)
after = total + 1
print(after)
`

	session := newDAPSession(t, python, "controls_py311_target.py", source)
	session.setBreakpoint(7)
	session.configurationDone()
	session.continueToBreakpoint()
	assertTopFrame(t, session.topFrame(), session.script, 7, "<module>")
}

// TestDAPContinueAdvancesWithinSameTraceMessageE2E records a tiny script with
// adjacent source breakpoints. These stops can share the same trace
// message_index, so continue must advance by full replay location, not by
// message_index alone.
func TestDAPContinueAdvancesWithinSameTraceMessageE2E(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}
	python := requirePython312(t)

	const source = `first = 1
second = 2
third = first + second
print(third)
`

	session := newDAPSession(t, python, "continue_same_message_target.py", source)
	session.setBreakpoints([]int{1, 2})
	session.configurationDone()
	session.continueToBreakpoint()
	assertTopFrame(t, session.topFrame(), session.script, 1, "<module>")

	session.continueToBreakpoint()
	assertTopFrame(t, session.topFrame(), session.script, 2, "<module>")
}

// TestDAPDockertestStyleFunctionBreakpointShowsUserFrameE2E records a
// dockertest-shaped script and stops at a source breakpoint inside its test()
// function. The top stack frame must be the user function, not the
// runpy/control callback stack.
func TestDAPDockertestStyleFunctionBreakpointShowsUserFrameE2E(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}
	python := requirePython312(t)

	const source = `"""
Simple test demonstrating automatic docker-compose.yml generation.
"""

def test():
    """Simple test that does not need any infrastructure."""
    print("=" * 60)
    print("Simple Test - No Infrastructure Needed")
    print("=" * 60)
    assert 1 + 1 == 2, "Math works!"
    print("Basic math: 1 + 1 = 2")

if __name__ == "__main__":
    test()
`

	scriptDir := filepath.Join(t.TempDir(), "retracesoftware_app", "src")
	session := newDAPSessionInScriptDir(t, python, scriptDir, "dockertest_style_target.py", source)
	session.setBreakpoint(11)
	session.configurationDone()
	session.continueToBreakpoint()
	assertTopFrame(t, session.topFrame(), session.script, 11, "test")
}

// TestDAPStepForwardThenBackToBeginningE2E records target_hello.py, walks
// forward with DAP next to the final module line, then walks backward with DAP
// stepBack and verifies the exact reverse line/function path.
func TestDAPStepForwardThenBackToBeginningE2E(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}
	python := requirePython312(t)

	session := newDAPSession(t, python, "target_hello.py", readExampleTargetHello(t))
	session.setBreakpoint(1)
	session.configurationDone()
	session.continueToBreakpoint()

	start := topFramePoint(t, session)
	if start != (dapFramePoint{Line: 1, Function: "<module>"}) {
		t.Fatalf("start point = %+v, want module line 1", start)
	}

	forward := []dapFramePoint{start}
	for i := 0; i < 80; i++ {
		session.step("next")
		point := topFramePoint(t, session)
		if point == forward[len(forward)-1] {
			t.Fatalf("forward walk stopped advancing before final line after %d steps at %+v; path: %+v",
				i+1, point, forward)
		}
		forward = append(forward, point)
		if point.Line == 12 && point.Function == "<module>" {
			t.Logf("forward reached final target_hello.py line after %d steps", i+1)
			break
		}
	}
	if len(forward) < 20 {
		t.Fatalf("forward walk too short (%d points): %+v", len(forward), forward)
	}
	if last := forward[len(forward)-1]; last.Line != 12 || last.Function != "<module>" {
		t.Fatalf("forward walk ended at %+v, want module line 12; path: %+v", last, forward)
	}

	backward := []dapFramePoint{forward[len(forward)-1]}
	for i := len(forward) - 2; i >= 0; i-- {
		session.step("stepBack")
		point := topFramePoint(t, session)
		backward = append(backward, point)
		if point != forward[i] {
			t.Fatalf("stepBack returned %+v, want forward[%d]=%+v; forward=%+v backward=%+v",
				point, i, forward[i], forward, backward)
		}
	}
	t.Logf("OK: walked %d points forward and %d points back", len(forward), len(backward))
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func formatMsgs(msgs []map[string]any) string {
	var s string
	for i, m := range msgs {
		b, _ := json.MarshalIndent(m, "  ", "  ")
		s += fmt.Sprintf("  [%d] %s\n", i, string(b))
	}
	return s
}
