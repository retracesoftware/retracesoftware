package replay

import (
	"bufio"
	"bytes"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestDAPStepBackPreservesStackAfterAssertionLine(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}
	python := requirePython312(t)
	tmpDir := t.TempDir()
	var logBuf bytes.Buffer
	oldLogOutput := log.Writer()
	log.SetOutput(io.MultiWriter(oldLogOutput, &logBuf))
	defer log.SetOutput(oldLogOutput)

	scriptDir := filepath.Join(tmpDir, "src")
	if err := os.MkdirAll(scriptDir, 0755); err != nil {
		t.Fatal(err)
	}
	scriptText := strings.TrimPrefix(`
import time


def normalize_discount(raw_promo):
    discount_bps = raw_promo["discount_bps"]
    discount_percent = discount_bps / 10  # BUG_LINE
    return discount_percent


def calculate_total(subtotal, discount_percent):
    discount = subtotal * (discount_percent / 100)
    total = subtotal - discount
    return round(total, 2)


def main():
    subtotal = 100.00
    promo = {"discount_bps": 1200}
    discount_percent = normalize_discount(promo)
    audit = {"checked_at": time.time(), "discount_percent": discount_percent}
    total = calculate_total(subtotal, discount_percent)
    print(f"audit={audit['discount_percent']}")
    print(f"total={total:.2f}")  # BEFORE_ASSERT_LINE
    assert total == -20.0, "checkout total went negative"  # ASSERT_LINE


if __name__ == "__main__":
    main()
`, "\n")

	script := filepath.Join(scriptDir, "checkout_stepback_target.py")
	if err := os.WriteFile(script, []byte(scriptText), 0644); err != nil {
		t.Fatal(err)
	}
	bugLine := lineContaining(t, scriptText, "BUG_LINE")
	beforeAssertLine := lineContaining(t, scriptText, "BEFORE_ASSERT_LINE")
	assertLine := lineContaining(t, scriptText, "ASSERT_LINE")

	tracePath := filepath.Join(tmpDir, "trace.retrace")
	pidFile, cleanup := extractPidFile(t, python, script, tracePath)
	defer cleanup()

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
	defer func() {
		client.send("disconnect", nil)
		client.readUntil(isResponse("disconnect"), 5)
		clientToProxyW.Close()
		select {
		case <-proxyDone:
		case <-time.After(5 * time.Second):
			t.Fatal("proxy did not shut down")
		}
	}()

	client.send("initialize", map[string]any{"clientID": "test", "adapterID": "retrace"})
	readSuccessfulResponse(t, client, "initialize", 5)
	if msgs, ok := client.readUntil(isEvent("initialized"), 5); !ok {
		t.Fatalf("never got initialized event; messages: %v", msgs)
	}

	client.send("launch", map[string]any{
		"type":      "retrace",
		"request":   "launch",
		"recording": pidFile,
	})
	readSuccessfulResponse(t, client, "launch", 10)

	client.send("setBreakpoints", map[string]any{
		"source":      map[string]any{"name": filepath.Base(script), "path": script},
		"lines":       []int{bugLine, assertLine},
		"breakpoints": []map[string]any{{"line": bugLine}, {"line": assertLine}},
	})
	readSuccessfulResponse(t, client, "setBreakpoints", 20)

	client.send("configurationDone", nil)
	waitStopped(t, client, "entry")

	client.send("continue", map[string]any{"threadId": 1})
	waitStopped(t, client, "breakpoint")
	if got := stackTopLine(t, client); got != bugLine {
		t.Fatalf("first continue stopped at line %d, want bug line %d", got, bugLine)
	}

	client.send("continue", map[string]any{"threadId": 1})
	waitStopped(t, client, "breakpoint")
	if got := stackTopLine(t, client); got != assertLine {
		t.Fatalf("second continue stopped at line %d, want assert line %d", got, assertLine)
	}

	// Baseline: reverse-continue works from the assertion breakpoint back to
	// the buggy conversion breakpoint.
	client.send("reverseContinue", map[string]any{"threadId": 1})
	waitStopped(t, client, "breakpoint")
	if got := stackTopLine(t, client); got != bugLine {
		t.Fatalf("reverseContinue stopped at line %d, want bug line %d", got, bugLine)
	}

	client.send("continue", map[string]any{"threadId": 1})
	waitStopped(t, client, "breakpoint")
	if got := stackTopLine(t, client); got != assertLine {
		t.Fatalf("continue after reverseContinue stopped at line %d, want assert line %d", got, assertLine)
	}

	// Regression: stepBack should leave the proxy at a materialisable cursor.
	// Henry's repro logs show PreviousStatement moving line 46->45, followed
	// by either a stackTrace materialisation failure or a hidden navigation
	// failure while still reporting a stopped event.
	client.send("stepBack", map[string]any{"threadId": 1})
	waitStopped(t, client, "step")
	if got := stackTopLine(t, client); got != beforeAssertLine {
		t.Fatalf("stepBack stopped at line %d, want line before assert %d", got, beforeAssertLine)
	}
	logs := logBuf.String()
	if strings.Contains(logs, "navigation failed (step)") || strings.Contains(logs, "stackTrace:") {
		t.Fatalf("stepBack reported a stopped event but logged cursor/stack failure:\n%s", logs)
	}
}

func readSuccessfulResponse(t *testing.T, client *dapClient, command string, limit int) map[string]any {
	t.Helper()
	msgs, ok := client.readUntil(isResponse(command), limit)
	if !ok {
		t.Fatalf("never got %s response; messages:\n%s", command, formatMsgs(msgs))
	}
	resp := msgs[len(msgs)-1]
	if resp["success"] != true {
		t.Fatalf("%s failed: %v", command, resp)
	}
	return resp
}

func lineContaining(t *testing.T, text, marker string) int {
	t.Helper()
	for i, line := range strings.Split(text, "\n") {
		if strings.Contains(line, marker) {
			return i + 1
		}
	}
	t.Fatalf("marker %q not found", marker)
	return 0
}

func waitStopped(t *testing.T, client *dapClient, reason string) {
	t.Helper()
	msgs, ok := client.readUntil(func(m map[string]any) bool {
		if m["type"] != "event" || m["event"] != "stopped" {
			return false
		}
		body, _ := m["body"].(map[string]any)
		return body["reason"] == reason
	}, 50)
	if !ok {
		t.Fatalf("never got stopped(%s); messages:\n%s", reason, formatMsgs(msgs))
	}
}

func stackTopLine(t *testing.T, client *dapClient) int {
	t.Helper()
	client.send("stackTrace", map[string]any{"threadId": 1})
	msgs, ok := client.readUntil(isResponse("stackTrace"), 20)
	if !ok {
		t.Fatalf("never got stackTrace response; messages:\n%s", formatMsgs(msgs))
	}
	resp := msgs[len(msgs)-1]
	if resp["success"] != true {
		t.Fatalf("stackTrace failed: %v", resp)
	}
	body, _ := resp["body"].(map[string]any)
	frames, _ := body["stackFrames"].([]any)
	if len(frames) == 0 {
		t.Fatalf("stackTrace returned no frames after navigation; response: %v", resp)
	}
	top, _ := frames[0].(map[string]any)
	line, ok := top["line"].(float64)
	if !ok {
		t.Fatalf("top stack frame has no numeric line: %v", top)
	}
	return int(line)
}
