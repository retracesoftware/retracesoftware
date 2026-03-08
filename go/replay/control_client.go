package replay

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

type ControlClient struct {
	conn   net.Conn
	reader *bufio.Reader
	writer *bufio.Writer

	mu     sync.Mutex
	closed bool
	seq    uint64
}

func NewControlClient(conn net.Conn) *ControlClient {
	return &ControlClient{
		conn:   conn,
		reader: bufio.NewReader(conn),
		writer: bufio.NewWriter(conn),
	}
}

func (c *ControlClient) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return nil
	}
	c.closed = true
	return c.conn.Close()
}

func (c *ControlClient) nextID() string {
	n := atomic.AddUint64(&c.seq, 1)
	return fmt.Sprintf("req-%d", n)
}

func (c *ControlClient) send(req ControlRequest) error {
	b, err := json.Marshal(req)
	if err != nil {
		return err
	}
	if _, err := c.writer.Write(b); err != nil {
		return err
	}
	if err := c.writer.WriteByte('\n'); err != nil {
		return err
	}
	return c.writer.Flush()
}

func (c *ControlClient) ReadMessage() (ControlMessage, error) {
	line, err := c.reader.ReadBytes('\n')
	if err != nil {
		return ControlMessage{}, err
	}
	return parseControlMessage(line)
}

func (c *ControlClient) Request(ctx context.Context, method string, params map[string]any) (ControlResponse, error) {
	reqID := c.nextID()
	req := ControlRequest{
		ID:     reqID,
		Method: method,
		Params: params,
	}

	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return ControlResponse{}, fmt.Errorf("control client is closed")
	}
	if err := c.send(req); err != nil {
		c.mu.Unlock()
		return ControlResponse{}, err
	}
	c.mu.Unlock()

	for {
		select {
		case <-ctx.Done():
			return ControlResponse{}, ctx.Err()
		default:
		}
		msg, err := c.ReadMessage()
		if err != nil {
			return ControlResponse{}, err
		}
		if msg.Kind == "event" || msg.Kind == "stop" || msg.Type == "event" {
			continue
		}
		if msg.ID != reqID {
			continue
		}
		resp := ControlResponse{
			ID:     msg.ID,
			OK:     msg.OK,
			Result: msg.Result,
			Error:  msg.Error,
		}
		if !resp.OK && resp.Error != nil {
			return resp, fmt.Errorf("%s: %s", resp.Error.Code, resp.Error.Message)
		}
		return resp, nil
	}
}

// SendCommand sends a request without waiting for a response.
// Used for streaming commands like hit_breakpoints where the
// caller reads events via ReadMessage.
func (c *ControlClient) SendCommand(method string, params map[string]any) (string, error) {
	reqID := c.nextID()
	req := ControlRequest{
		ID:     reqID,
		Method: method,
		Params: params,
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return "", fmt.Errorf("control client is closed")
	}
	return reqID, c.send(req)
}

func (c *ControlClient) RunToCursor(ctx context.Context, cursor RawCursor) (ControlStopResult, error) {
	if _, err := c.SendCommand("run_to_cursor", map[string]any{"cursor": cursor.ToMap()}); err != nil {
		return ControlStopResult{}, err
	}
	for {
		select {
		case <-ctx.Done():
			return ControlStopResult{}, ctx.Err()
		default:
		}
		msg, err := c.ReadMessage()
		if err != nil {
			return ControlStopResult{}, err
		}
		if msg.Error != nil {
			return ControlStopResult{}, fmt.Errorf("run_to_cursor: %s: %s", msg.Error.Code, msg.Error.Message)
		}
		if msg.Kind == "stop" {
			return parseStopResult(msg.Payload), nil
		}
	}
}

// StartControlProcess starts python replay in control-protocol mode and returns
// a connected ControlClient plus the subprocess handle.
func StartControlProcess(target runnerTarget, stdout, stderr io.Writer) (*ControlClient, *os.Process, func(), error) {
	dir, err := os.MkdirTemp("", "retrace-control-")
	if err != nil {
		return nil, nil, nil, err
	}
	cleanup := func() { _ = os.RemoveAll(dir) }
	socketPath := filepath.Join(dir, "control.sock")

	ln, err := net.Listen("unix", socketPath)
	if err != nil {
		cleanup()
		return nil, nil, nil, err
	}

	args := []string{"-m", "retracesoftware", "--recording", target.Recording, "--control_socket", socketPath}
	log.Printf("python: %s  cwd: %s  socket: %s", target.PythonBin, target.CWD, socketPath)
	cmd := buildCommand(target.PythonBin, args...)
	cmd.Dir = target.CWD
	cmd.Stdin = nil
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	if err := startCommand(cmd); err != nil {
		_ = ln.Close()
		cleanup()
		return nil, nil, nil, err
	}

	deadline := time.Now().Add(10 * time.Second)
	_ = ln.(*net.UnixListener).SetDeadline(deadline)
	conn, err := ln.Accept()
	_ = ln.Close()
	if err != nil {
		_ = cmd.Process.Kill()
		_ = waitCommand(cmd)
		cleanup()
		return nil, nil, nil, err
	}

	client := NewControlClient(conn)
	return client, cmd.Process, cleanup, nil
}

var startControlProcess = StartControlProcess
