package replay

import (
	"context"
	"fmt"
	"io"
	"log"
	"os"
	"runtime"
)

// StartReplayFromPidFile reads a PidFile's preamble and starts a single
// replay process from the beginning of the trace.
func StartReplayFromPidFile(ctx context.Context, pidFile string, stdout, stderr io.Writer) (*Replay, error) {
	process, err := ReadProcess(pidFile)
	if err != nil {
		return nil, fmt.Errorf("read pidfile preamble: %w", err)
	}
	target, err := targetFromProcess(process)
	if err != nil {
		return nil, err
	}
	return StartReplay(ctx, target, stdout, stderr)
}

// Replay represents a running Python replay process with an active control
// protocol connection. It owns the subprocess, the socket connection, and
// cleanup resources. All communication to the target process goes through
// this type.
type Replay struct {
	client    *ControlClient
	proc      *os.Process
	cleanup   func()
	forks     *AwaitingCollection
	ownsForks bool
	location  Location
}

// Location returns the trace position this replay was last navigated to.
func (r *Replay) Location() Location { return r.location }

// startReplayProcess is the internal hook used by StartReplay, replaceable
// in tests to inject a mock connection.
var startReplayProcess = startControlProcess

// StartReplay launches a Python replay process, connects via the control
// socket, and performs the hello handshake. The returned Replay is ready
// for commands. Callers must call Close when done.
func StartReplay(ctx context.Context, target runnerTarget, stdout, stderr io.Writer) (*Replay, error) {
	client, proc, cleanup, err := startReplayProcess(target, stdout, stderr)
	if err != nil {
		return nil, fmt.Errorf("start replay process: %w", err)
	}
	ac, err := NewAwaitingCollection()
	if err != nil {
		if proc != nil {
			_ = proc.Kill()
		}
		cleanup()
		return nil, fmt.Errorf("create fork collection: %w", err)
	}
	r := &Replay{client: client, proc: proc, cleanup: cleanup, forks: ac, ownsForks: true}
	runtime.SetFinalizer(r, (*Replay).Close)

	if _, err := client.Request(ctx, "hello", nil); err != nil {
		r.Close()
		return nil, fmt.Errorf("hello: %w", err)
	}
	return r, nil
}

// FindBreakpoints sends a hit_breakpoints command and streams breakpoint
// hit locations. The channel closes when the replay stops, reaches EOF,
// or ctx is cancelled.
func (r *Replay) FindBreakpoints(ctx context.Context, breakpoint map[string]any) (<-chan Location, <-chan error) {
	hits := make(chan Location)
	errs := make(chan error, 1)

	go func() {
		defer close(hits)
		defer close(errs)

		if _, err := r.client.SendCommand("hit_breakpoints", map[string]any{
			"breakpoint": breakpoint,
		}); err != nil {
			errs <- fmt.Errorf("hit_breakpoints: %w", err)
			return
		}

		for {
			select {
			case <-ctx.Done():
				errs <- context.Canceled
				return
			default:
			}
			msg, err := r.client.ReadMessage()
			if err != nil {
				if err == io.EOF {
					return
				}
				errs <- fmt.Errorf("read: %w", err)
				return
			}
			if msg.Kind == "event" && msg.Event == "log" {
				if text, ok := msg.Payload["message"].(string); ok {
					log.Printf("python: %s", text)
				}
				continue
			}
			if msg.Kind == "event" && msg.Event == "breakpoint_hit" {
				loc := parseLocationFromPayload(msg.Payload)
				select {
				case <-ctx.Done():
					errs <- context.Canceled
					return
				case hits <- loc:
				}
				continue
			}
			if msg.Kind == "stop" {
				return
			}
		}
	}()

	return hits, errs
}

// FindFirstBreakpoint sends hit_breakpoints with max_hits=1 and returns
// the first hit as a Location. Returns nil (with no error) if the replay
// reaches EOF without hitting the breakpoint.
func (r *Replay) FindFirstBreakpoint(ctx context.Context, breakpoint map[string]any) (*Location, error) {
	if _, err := r.client.SendCommand("hit_breakpoints", map[string]any{
		"breakpoint": breakpoint,
		"max_hits":   1,
	}); err != nil {
		return nil, fmt.Errorf("hit_breakpoints: %w", err)
	}

	for {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}
		msg, err := r.client.ReadMessage()
		if err != nil {
			if err == io.EOF {
				return nil, nil
			}
			return nil, fmt.Errorf("read: %w", err)
		}
		if msg.Kind == "event" && msg.Event == "breakpoint_hit" {
			loc := parseLocationFromPayload(msg.Payload)
			return &loc, nil
		}
		if msg.Kind == "stop" {
			return nil, nil
		}
		if msg.ID != "" && msg.OK {
			return nil, nil
		}
	}
}

// RunToCursor sends a run_to_cursor command and blocks until the replay
// stops, returning the raw protocol stop result. It also updates the
// Replay's current Location from the stop result.
func (r *Replay) RunToCursor(ctx context.Context, cursor RawCursor) (ControlStopResult, error) {
	result, err := r.client.RunToCursor(ctx, cursor)
	if err == nil {
		r.location = Location{
			ThreadID:       result.Cursor.ThreadID,
			FunctionCounts: result.Cursor.FunctionCounts,
			FLasti:         result.Cursor.FLasti,
			MessageIndex:   result.MessageIndex,
		}
	}
	return result, err
}

// SetBackstop sets the backstop message index on the replay.
func (r *Replay) SetBackstop(ctx context.Context, messageIndex int) error {
	_, err := r.client.Request(ctx, "set_backstop", map[string]any{
		"message_index": messageIndex,
	})
	return err
}

// RunToReturn sends a run_to_return command and collects the streamed
// cursor events as Locations. It returns the collected locations and the
// final stop result (reason is "return", "backstop", or
// "call_counter_limit").
func (r *Replay) RunToReturn(ctx context.Context, maxCallCounter *int) ([]Location, ControlStopResult, error) {
	params := map[string]any{}
	if maxCallCounter != nil {
		params["max_call_counter"] = *maxCallCounter
	}
	if _, err := r.client.SendCommand("run_to_return", params); err != nil {
		return nil, ControlStopResult{}, fmt.Errorf("run_to_return: %w", err)
	}

	var locations []Location
	for {
		select {
		case <-ctx.Done():
			return locations, ControlStopResult{}, ctx.Err()
		default:
		}
		msg, err := r.client.ReadMessage()
		if err != nil {
			if err == io.EOF {
				return locations, ControlStopResult{Reason: "eof"}, nil
			}
			return locations, ControlStopResult{}, fmt.Errorf("read: %w", err)
		}
		if msg.Kind == "event" && msg.Event == "cursor" {
			locations = append(locations, parseLocationFromPayload(msg.Payload))
			continue
		}
		if msg.Kind == "stop" {
			return locations, parseStopResult(msg.Payload), nil
		}
	}
}

// NextInstruction sends a next_instruction command that advances exactly
// one bytecode instruction within the current function and returns the
// resulting Location. The replay must already be positioned (e.g. via
// RunToCursor) before calling this.
func (r *Replay) NextInstruction(ctx context.Context) (Location, error) {
	if _, err := r.client.SendCommand("next_instruction", nil); err != nil {
		return Location{}, fmt.Errorf("next_instruction: %w", err)
	}
	for {
		select {
		case <-ctx.Done():
			return Location{}, ctx.Err()
		default:
		}
		msg, err := r.client.ReadMessage()
		if err != nil {
			if err == io.EOF {
				return Location{}, fmt.Errorf("next_instruction: unexpected EOF")
			}
			return Location{}, fmt.Errorf("read: %w", err)
		}
		if msg.Kind == "stop" {
			stop := parseStopResult(msg.Payload)
			loc := Location{
				ThreadID:       stop.Cursor.ThreadID,
				FunctionCounts: stop.Cursor.FunctionCounts,
				FLasti:         stop.Cursor.FLasti,
				MessageIndex:   stop.MessageIndex,
			}
			r.location = loc
			return loc, nil
		}
	}
}

// InstructionToLineno sends an instruction_to_lineno command and returns
// a flat list mapping instruction index (offset // 2) to source line number
// for the code object of the currently stopped function.
func (r *Replay) InstructionToLineno(ctx context.Context) ([]int, error) {
	resp, err := r.client.Request(ctx, "instruction_to_lineno", nil)
	if err != nil {
		return nil, fmt.Errorf("instruction_to_lineno: %w", err)
	}
	raw, ok := resp.Result["linenos"].([]any)
	if !ok {
		return nil, fmt.Errorf("instruction_to_lineno: missing linenos in response")
	}
	linenos := make([]int, len(raw))
	for i, v := range raw {
		if n, ok := v.(float64); ok {
			linenos[i] = int(n)
		}
	}
	return linenos, nil
}

// Stack asks the Python inspector for the current call stack.
// Each frame is a map with keys like "filename", "line", "function".
func (r *Replay) Stack(ctx context.Context) ([]map[string]any, error) {
	resp, err := r.client.Request(ctx, "stack", nil)
	if err != nil {
		return nil, fmt.Errorf("stack: %w", err)
	}
	return parseFrameList(resp.Result, "frames")
}

// Locals asks the Python inspector for local variables in the current frame.
// Each variable is a map with keys like "name", "value", "type".
func (r *Replay) Locals(ctx context.Context) ([]map[string]any, error) {
	resp, err := r.client.Request(ctx, "locals", nil)
	if err != nil {
		return nil, fmt.Errorf("locals: %w", err)
	}
	return parseFrameList(resp.Result, "variables")
}

// SourceLocation asks the Python inspector for the source file and line
// of the current stopped position.
func (r *Replay) SourceLocation(ctx context.Context) (map[string]any, error) {
	resp, err := r.client.Request(ctx, "source_location", nil)
	if err != nil {
		return nil, fmt.Errorf("source_location: %w", err)
	}
	return resp.Result, nil
}

func parseFrameList(result map[string]any, key string) ([]map[string]any, error) {
	raw, ok := result[key].([]any)
	if !ok {
		return nil, nil
	}
	out := make([]map[string]any, 0, len(raw))
	for _, item := range raw {
		if m, ok := item.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out, nil
}

// fork snapshots the running replay process via os.fork on the Python side.
// The returned Replay is a new process at the same trace position. Both the
// original and the fork can accept commands independently.
func (r *Replay) fork(ctx context.Context) (*Replay, error) {
	resp, err := r.client.Request(ctx, "fork", map[string]any{
		"socket_path": r.forks.SocketPath(),
	})
	if err != nil {
		return nil, fmt.Errorf("fork request: %w", err)
	}

	var childPID int
	if pid, ok := resp.Result["pid"].(float64); ok {
		childPID = int(pid)
	}

	childClient, err := r.forks.Collect(ctx, childPID)
	if err != nil {
		return nil, fmt.Errorf("collect fork child %d: %w", childPID, err)
	}

	childProc, _ := os.FindProcess(childPID)
	child := &Replay{
		client:   childClient,
		proc:     childProc,
		forks:    r.forks,
		location: r.location,
	}
	runtime.SetFinalizer(child, (*Replay).Close)
	return child, nil
}

// Close kills the replay process and releases all resources.
func (r *Replay) Close() error {
	if r.client != nil {
		_ = r.client.Close()
	}
	if r.proc != nil {
		_ = r.proc.Kill()
	}
	if r.forks != nil && r.ownsForks {
		_ = r.forks.Close()
	}
	if r.cleanup != nil {
		r.cleanup()
	}
	return nil
}
