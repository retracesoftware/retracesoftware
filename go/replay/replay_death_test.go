package replay

import (
	"errors"
	"net"
	"os"
	"os/exec"
	"runtime"
	"testing"
	"time"
)

// TestReplayProcessDied verifies that killing the Python subprocess
// causes all subsequent operations to return ErrReplayProcessDied and
// that the dead channel is closed promptly.
func TestReplayProcessDied(t *testing.T) {
	// Start a long-running subprocess that we can kill.
	// Use "sleep 60" — it just needs to live long enough for us to
	// connect and then die when we kill it.
	var sleepCmd string
	if runtime.GOOS == "windows" {
		t.Skip("test requires unix process management")
	}
	sleepCmd = "sleep"

	cmd := exec.Command(sleepCmd, "60")
	if err := cmd.Start(); err != nil {
		t.Fatalf("start sleep: %v", err)
	}

	// Create a connected socket pair to act as the control connection.
	s1, s2, err := socketPair()
	if err != nil {
		_ = cmd.Process.Kill()
		t.Fatalf("socketpair: %v", err)
	}
	defer s2.Close()

	client := NewControlClient(s1)

	r := &Replay{
		client: client,
		proc:   cmd.Process,
		dead:   make(chan struct{}),
	}
	go r.watchProcess()

	// Err should be nil while process is alive.
	if err := r.Err(); err != nil {
		t.Fatalf("expected nil Err before kill, got %v", err)
	}

	// Kill the subprocess.
	if err := cmd.Process.Kill(); err != nil {
		t.Fatalf("kill: %v", err)
	}

	// Wait for the dead channel to close.
	select {
	case <-r.Dead():
	case <-time.After(5 * time.Second):
		t.Fatal("dead channel not closed within 5s after kill")
	}

	// Err should now return ErrReplayProcessDied.
	if err := r.Err(); err == nil {
		t.Fatal("expected non-nil Err after kill")
	} else if !errors.Is(err, ErrReplayProcessDied) {
		t.Fatalf("expected ErrReplayProcessDied, got %v", err)
	} else {
		t.Logf("Err() = %v", err)
	}

	// wrapErr should substitute the exit error.
	wrapped := r.wrapErr(os.ErrClosed)
	if !errors.Is(wrapped, ErrReplayProcessDied) {
		t.Fatalf("wrapErr should return ErrReplayProcessDied, got %v", wrapped)
	}

	// Close should be safe to call multiple times.
	if err := r.Close(); err != nil {
		t.Fatalf("first Close: %v", err)
	}
	if err := r.Close(); err != nil {
		t.Fatalf("second Close: %v", err)
	}
}

// TestReplayProcessDied_BlockedRead verifies that a ReadMessage blocked
// on the socket is unblocked when the process dies and watchProcess
// closes the client.
func TestReplayProcessDied_BlockedRead(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test requires unix process management")
	}

	cmd := exec.Command("sleep", "60")
	if err := cmd.Start(); err != nil {
		t.Fatalf("start sleep: %v", err)
	}

	s1, s2, err := socketPair()
	if err != nil {
		_ = cmd.Process.Kill()
		t.Fatalf("socketpair: %v", err)
	}
	defer s2.Close()

	client := NewControlClient(s1)

	r := &Replay{
		client: client,
		proc:   cmd.Process,
		dead:   make(chan struct{}),
	}
	go r.watchProcess()

	// Start a goroutine that blocks on ReadMessage.
	readErr := make(chan error, 1)
	go func() {
		_, err := r.client.ReadMessage()
		readErr <- err
	}()

	// Give the read a moment to block, then kill.
	time.Sleep(50 * time.Millisecond)
	_ = cmd.Process.Kill()

	// The read should unblock with an error.
	select {
	case err := <-readErr:
		if err == nil {
			t.Fatal("expected error from ReadMessage after kill, got nil")
		}
		t.Logf("ReadMessage error after kill: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("ReadMessage did not unblock within 5s after kill")
	}

	// Err should report the process death.
	if exitErr := r.Err(); exitErr == nil || !errors.Is(exitErr, ErrReplayProcessDied) {
		t.Fatalf("expected ErrReplayProcessDied, got %v", exitErr)
	}

	r.Close()
}

func socketPair() (net.Conn, net.Conn, error) {
	ln, err := net.Listen("unix", "")
	if err != nil {
		// Fallback: use a temp file path.
		f, ferr := os.CreateTemp("", "retrace-test-*.sock")
		if ferr != nil {
			return nil, nil, ferr
		}
		path := f.Name()
		f.Close()
		os.Remove(path)
		ln, err = net.Listen("unix", path)
		if err != nil {
			return nil, nil, err
		}
	}
	defer ln.Close()

	done := make(chan net.Conn, 1)
	go func() {
		c, _ := ln.Accept()
		done <- c
	}()

	c1, err := net.Dial("unix", ln.Addr().String())
	if err != nil {
		return nil, nil, err
	}
	c2 := <-done
	return c1, c2, nil
}
