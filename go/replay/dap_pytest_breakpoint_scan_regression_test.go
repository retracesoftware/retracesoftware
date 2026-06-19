package replay

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func newDAPSessionForPidFile(t *testing.T, pidFile, sourcePath string) *dapSession {
	t.Helper()

	clientToProxyR, clientToProxyW := io.Pipe()
	proxyToClientR, proxyToClientW := io.Pipe()

	dapWriter := NewWriter(proxyToClientW)
	proxy := NewProxy(pidFile, clientToProxyR, dapWriter)
	proxy.navTimeout = 10 * time.Second

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
		script:         sourcePath,
	}
	t.Cleanup(session.close)

	session.initialize()
	session.launch(pidFile)
	return session
}

func recordFailingPytestTrace(t *testing.T, python, projectDir, tracePath string) {
	t.Helper()

	cmd := pythonCommand(
		python,
		"-m",
		"retracesoftware",
		"--recording",
		tracePath,
		"--stacktraces",
		"--",
		"-m",
		"pytest",
		"tests",
		"-q",
		"--tb=short",
	)
	cmd.Dir = projectDir
	cmd.Env = prependEnvPath(cmd.Env, "PYTHONPATH", []string{projectDir})
	cmd.Env = append(cmd.Env, "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1")

	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Fatalf("expected pytest recording to fail, but it passed\noutput:\n%s", out)
	}
	exitErr, ok := err.(*exec.ExitError)
	if !ok || exitErr.ExitCode() != 1 {
		t.Fatalf("pytest recording failed unexpectedly: %v\noutput:\n%s", err, out)
	}
	if !strings.Contains(string(out), "assert 3 == 4") {
		t.Fatalf("pytest did not report the expected failure\noutput:\n%s", out)
	}
	if stat, err := os.Stat(tracePath); err != nil || stat.Size() == 0 {
		t.Fatalf("trace file missing or empty after recording: %v", err)
	}
}

func writePytestBreakpointScanProject(t *testing.T, projectDir string) (string, int) {
	t.Helper()

	if err := os.MkdirAll(filepath.Join(projectDir, "tests"), 0755); err != nil {
		t.Fatal(err)
	}

	sourcePath := filepath.Join(projectDir, "app.py")
	source := `def calculate_total(value):
    adjusted = value + 2
    result = adjusted  # retrace-breakpoint
    return result
`
	if err := os.WriteFile(sourcePath, []byte(source), 0644); err != nil {
		t.Fatal(err)
	}

	testSource := `from app import calculate_total


def test_failing_pytest_case():
    assert calculate_total(1) == 4
`
	if err := os.WriteFile(filepath.Join(projectDir, "tests", "test_app.py"), []byte(testSource), 0644); err != nil {
		t.Fatal(err)
	}

	for i, line := range strings.Split(source, "\n") {
		if strings.Contains(line, "retrace-breakpoint") {
			return sourcePath, i + 1
		}
	}
	t.Fatal("breakpoint marker missing")
	return "", 0
}

func extractRootPidFile(t *testing.T, tracePath string) (string, func()) {
	t.Helper()

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

func TestDAPPytestBreakpointScanStopsInMinimalRecordedPytestFailure(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}

	python := requirePython312(t)
	if err := pythonCommand(python, "-c", "import pytest").Run(); err != nil {
		t.Skipf("pytest is not installed in %s: %v", python, err)
	}

	projectDir := t.TempDir()
	if resolved, err := filepath.EvalSymlinks(projectDir); err == nil {
		projectDir = resolved
	}
	sourcePath, breakpointLine := writePytestBreakpointScanProject(t, projectDir)

	tracePath := filepath.Join(t.TempDir(), "pytest-failure.retrace")
	recordFailingPytestTrace(t, python, projectDir, tracePath)

	pidFile, cleanup := extractRootPidFile(t, tracePath)
	defer cleanup()

	session := newDAPSessionForPidFile(t, pidFile, sourcePath)
	session.setBreakpoint(breakpointLine)
	session.configurationDone()
	session.continueToBreakpoint()

	frame := session.topFrame()
	assertTopFrame(t, frame, sourcePath, breakpointLine, "calculate_total")
}

func TestDAPPytestBreakpointScanExternalRecordingRegression(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}

	pidFile := os.Getenv("RETRACE_DAP_PYTEST_PIDFILE")
	sourcePath := os.Getenv("RETRACE_DAP_PYTEST_SOURCE")
	lineValue := os.Getenv("RETRACE_DAP_PYTEST_LINE")
	if pidFile == "" || sourcePath == "" || lineValue == "" {
		t.Skip("set RETRACE_DAP_PYTEST_PIDFILE, RETRACE_DAP_PYTEST_SOURCE, and RETRACE_DAP_PYTEST_LINE to reproduce a pytest DAP breakpoint-scan failure from an extracted pidfile")
	}

	breakpointLine, err := strconv.Atoi(lineValue)
	if err != nil || breakpointLine <= 0 {
		t.Fatalf("invalid RETRACE_DAP_PYTEST_LINE=%q", lineValue)
	}
	if _, err := os.Stat(pidFile); err != nil {
		t.Fatalf("pidfile does not exist: %s: %v", pidFile, err)
	}
	if !sourceLineCanBreak(sourcePath, breakpointLine) {
		t.Fatalf("source line cannot break: %s:%d", sourcePath, breakpointLine)
	}

	session := newDAPSessionForPidFile(t, pidFile, sourcePath)
	session.setBreakpoint(breakpointLine)
	session.configurationDone()
	session.continueToBreakpoint()

	frame := session.topFrame()
	assertTopFrame(t, frame, sourcePath, breakpointLine, "")
}
