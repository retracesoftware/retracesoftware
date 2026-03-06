package replay

import (
	"context"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"sync"
)

// AwaitingCollection accepts forked child connections on a persistent
// listener, reads their fork_hello event, and parks them by PID.
// Callers retrieve a connection with Collect(pid), which removes it
// from the pool.
type AwaitingCollection struct {
	mu      sync.Mutex
	parked  map[int]*ControlClient
	waiters map[int]chan *ControlClient

	listener   net.Listener
	socketPath string
	dir        string
}

// NewAwaitingCollection creates a Unix listener in a temp directory and
// starts a background goroutine that accepts connections and parks them
// by PID from the fork_hello event.
func NewAwaitingCollection() (*AwaitingCollection, error) {
	dir, err := os.MkdirTemp("", "retrace-fork-")
	if err != nil {
		return nil, fmt.Errorf("create fork dir: %w", err)
	}
	socketPath := filepath.Join(dir, "fork.sock")

	ln, err := net.Listen("unix", socketPath)
	if err != nil {
		_ = os.RemoveAll(dir)
		return nil, fmt.Errorf("listen fork socket: %w", err)
	}

	ac := &AwaitingCollection{
		parked:     make(map[int]*ControlClient),
		waiters:    make(map[int]chan *ControlClient),
		listener:   ln,
		socketPath: socketPath,
		dir:        dir,
	}
	go ac.acceptLoop()
	return ac, nil
}

// SocketPath returns the Unix socket path that forked children should
// connect to.
func (ac *AwaitingCollection) SocketPath() string {
	return ac.socketPath
}

func (ac *AwaitingCollection) acceptLoop() {
	for {
		conn, err := ac.listener.Accept()
		if err != nil {
			return
		}
		client := NewControlClient(conn)
		msg, err := client.ReadMessage()
		if err != nil || msg.Event != "fork_hello" {
			_ = client.Close()
			continue
		}
		pid := 0
		if p, ok := msg.Payload["pid"].(float64); ok {
			pid = int(p)
		}

		ac.mu.Lock()
		if ch, ok := ac.waiters[pid]; ok {
			delete(ac.waiters, pid)
			ac.mu.Unlock()
			ch <- client
		} else {
			ac.parked[pid] = client
			ac.mu.Unlock()
		}
	}
}

// Collect blocks until the forked child with the given PID connects,
// then returns its ControlClient and removes it from the pool.
func (ac *AwaitingCollection) Collect(ctx context.Context, pid int) (*ControlClient, error) {
	ac.mu.Lock()
	if client, ok := ac.parked[pid]; ok {
		delete(ac.parked, pid)
		ac.mu.Unlock()
		return client, nil
	}
	ch := make(chan *ControlClient, 1)
	ac.waiters[pid] = ch
	ac.mu.Unlock()

	select {
	case <-ctx.Done():
		ac.mu.Lock()
		delete(ac.waiters, pid)
		ac.mu.Unlock()
		return nil, ctx.Err()
	case client := <-ch:
		return client, nil
	}
}

// Close stops the accept loop and cleans up all parked connections.
func (ac *AwaitingCollection) Close() error {
	err := ac.listener.Close()

	ac.mu.Lock()
	defer ac.mu.Unlock()
	for pid, client := range ac.parked {
		_ = client.Close()
		delete(ac.parked, pid)
	}
	_ = os.RemoveAll(ac.dir)
	return err
}
