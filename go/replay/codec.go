// Package replay implements DAP (Debug Adapter Protocol) message framing
// and proxy logic.
package replay

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"strconv"
	"strings"
	"sync"
)

// ReadMessage reads one Content-Length framed DAP message from r.
// Returns the raw JSON body. Returns nil, io.EOF on clean stream close.
func ReadMessage(r *bufio.Reader) (json.RawMessage, error) {
	contentLength := -1

	for {
		line, err := r.ReadString('\n')
		if err != nil {
			if contentLength > 0 {
				return nil, fmt.Errorf("unexpected EOF in headers: %w", err)
			}
			return nil, io.EOF
		}
		line = strings.TrimRight(line, "\r\n")
		if line == "" {
			break
		}
		if strings.HasPrefix(line, "Content-Length:") {
			val := strings.TrimSpace(line[len("Content-Length:"):])
			n, err := strconv.Atoi(val)
			if err != nil {
				return nil, fmt.Errorf("bad Content-Length %q: %w", val, err)
			}
			contentLength = n
		}
	}

	if contentLength < 0 {
		return nil, io.EOF
	}

	body := make([]byte, contentLength)
	if _, err := io.ReadFull(r, body); err != nil {
		return nil, fmt.Errorf("short body read: %w", err)
	}

	return json.RawMessage(body), nil
}

// WriteMessage writes one Content-Length framed DAP message to w.
func WriteMessage(w io.Writer, msg json.RawMessage) error {
	header := fmt.Sprintf("Content-Length: %d\r\n\r\n", len(msg))
	if _, err := io.WriteString(w, header); err != nil {
		return err
	}
	if _, err := w.Write(msg); err != nil {
		return err
	}
	if f, ok := w.(interface{ Flush() error }); ok {
		return f.Flush()
	}
	return nil
}

// Writer is a thread-safe DAP message writer.
type Writer struct {
	mu sync.Mutex
	bw *bufio.Writer
}

// NewWriter wraps w in a thread-safe buffered DAP writer.
func NewWriter(w io.Writer) *Writer {
	return &Writer{bw: bufio.NewWriter(w)}
}

// Write sends a single DAP message, flushing the buffer.
func (w *Writer) Write(msg json.RawMessage) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	return WriteMessage(w.bw, msg)
}

// DAPLogWriter implements io.Writer by emitting each Write as a DAP
// "output" event with category "console". Plug it into log.SetOutput
// so that log.Printf lines appear in the VSCode Debug Console.
type DAPLogWriter struct {
	w *Writer
}

func NewDAPLogWriter(w *Writer) *DAPLogWriter {
	return &DAPLogWriter{w: w}
}

func (d *DAPLogWriter) Write(p []byte) (int, error) {
	msg := makeOutputEvent(string(p))
	if err := d.w.Write(msg); err != nil {
		return 0, err
	}
	return len(p), nil
}

func makeOutputEvent(text string) json.RawMessage {
	m := map[string]any{
		"seq":   1,
		"type":  "event",
		"event": "output",
		"body": map[string]any{
			"category": "console",
			"output":   text,
		},
	}
	out, _ := json.Marshal(m)
	return out
}
