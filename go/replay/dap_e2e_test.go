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
// Checks well-known venv locations and PATH.
func findPython312() (string, error) {
	candidates := []string{
		"/Users/nathanmatthews/Documents/venv/3.12.10/bin/python3",
	}
	// Also try PATH-based python3
	if p, err := exec.LookPath("python3"); err == nil {
		candidates = append(candidates, p)
	}

	for _, p := range candidates {
		if _, err := os.Stat(p); err != nil {
			continue
		}
		out, err := exec.Command(p, "-c",
			"import sys; v=sys.version_info; print(f'{v.major}.{v.minor}'); import retracesoftware").CombinedOutput()
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

	// 6. disconnect
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
