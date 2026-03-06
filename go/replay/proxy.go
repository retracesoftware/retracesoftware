package replay

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
)

// envelope is the minimal structure we unmarshal for routing decisions.
type envelope struct {
	Seq       int             `json:"seq"`
	Type      string          `json:"type"`
	Command   string          `json:"command,omitempty"`
	Arguments json.RawMessage `json:"arguments,omitempty"`
}

type dapBreakpoint struct {
	Line      int    `json:"line"`
	Condition string `json:"condition,omitempty"`
}

type dapSource struct {
	Path string `json:"path"`
}

type setBreakpointsArgs struct {
	Source      dapSource       `json:"source"`
	Breakpoints []dapBreakpoint `json:"breakpoints"`
}

// Proxy is the Go-owned DAP endpoint that maps requests to Debugger calls.
type Proxy struct {
	pidFile      string
	recordingDir string // directory the recording was made from
	debugger     Debugger
	provider     SnapshotProvider
	clientR      *bufio.Reader
	clientW      *Writer
	launchErr    error

	breakpointIDs       map[string]int // "file:line[:cond]" -> debugger breakpoint ID
	currentMessageIndex uint64
	currentCursor       *Cursor // current position, nil until first navigation
}

func NewProxy(pidFile string, clientIn io.Reader, clientW *Writer) *Proxy {
	// pidFile is <recording>.d/<pid>.bin — parent of parent is the recording dir
	recDir := filepath.Dir(filepath.Dir(pidFile))
	return &Proxy{
		pidFile:       pidFile,
		recordingDir:  recDir,
		clientR:       bufio.NewReader(clientIn),
		clientW:       clientW,
		breakpointIDs: make(map[string]int),
	}
}

// SetLaunchError records a pre-launch error (e.g. extraction failure)
// that will be reported as a DAP error response to the launch request.
func (p *Proxy) SetLaunchError(err error) { p.launchErr = err }

// SetDebugger overrides the debugger used by this proxy.
func (p *Proxy) SetDebugger(d Debugger) {
	if d != nil {
		p.debugger = d
	}
}

// Run processes DAP messages until the session ends.
func (p *Proxy) Run() error {
	if err := p.handlePreLaunch(); err != nil {
		return fmt.Errorf("pre-launch: %w", err)
	}
	return p.handlePostLaunch()
}

func (p *Proxy) handlePreLaunch() error {
	for {
		msg, err := ReadMessage(p.clientR)
		if err != nil {
			return err
		}

		var env envelope
		if err := json.Unmarshal(msg, &env); err != nil {
			return fmt.Errorf("bad message: %w", err)
		}

		if env.Type != "request" {
			continue
		}

		switch env.Command {
		case "initialize":
			if err := p.handleInitialize(env); err != nil {
				return err
			}
		case "launch":
			if err := p.handleLaunch(env); err != nil {
				return err
			}
			return nil
		default:
			resp := errorResponse(env.Seq, env.Command,
				fmt.Sprintf("request %q not supported before launch", env.Command))
			if err := p.clientW.Write(resp); err != nil {
				return err
			}
		}
	}
}

func (p *Proxy) handleInitialize(env envelope) error {
	caps := json.RawMessage(`{
		"supportsConfigurationDoneRequest": true,
		"supportsFunctionBreakpoints": true,
		"supportsConditionalBreakpoints": true,
		"supportsStepBack": true,
		"supportsStepInTargetsRequest": false,
		"supportsGotoTargetsRequest": true,
		"supportsRestartRequest": true,
		"supportsExceptionInfoRequest": true,
		"supportsSteppingGranularity": true,
		"exceptionBreakpointFilters": [
			{"filter": "raised", "label": "Raised Exceptions"},
			{"filter": "uncaught", "label": "Uncaught Exceptions", "default": true}
		]
	}`)

	resp := successResponse(env.Seq, "initialize", caps)
	if err := p.clientW.Write(resp); err != nil {
		return err
	}
	return p.clientW.Write(makeEvent("initialized", nil))
}

func (p *Proxy) handleLaunch(env envelope) error {
	if p.launchErr != nil {
		return p.clientW.Write(errorResponse(env.Seq, "launch", p.launchErr.Error()))
	}
	if p.pidFile == "" {
		return p.clientW.Write(errorResponse(env.Seq, "launch", "no pidFile configured"))
	}
	log.Printf("launch: pidFile=%s", p.pidFile)

	if p.debugger == nil {
		ctx := context.Background()
		root, err := StartReplayFromPidFile(ctx, p.pidFile, nil, os.Stderr)
		if err != nil {
			return p.clientW.Write(errorResponse(env.Seq, "launch", err.Error()))
		}
		engine := NewQueryEngine(root, p.pidFile)
		p.debugger = NewDebugger(engine)
		p.provider = engine.Provider()
	}

	return p.clientW.Write(successResponse(env.Seq, "launch", nil))
}

func (p *Proxy) handlePostLaunch() error {
	if p.pidFile == "" {
		return fmt.Errorf("launch target was not initialized")
	}
	for {
		msg, err := ReadMessage(p.clientR)
		if err != nil {
			return fmt.Errorf("client read: %w", err)
		}

		var env envelope
		if err := json.Unmarshal(msg, &env); err != nil {
			continue
		}
		if env.Type != "request" {
			continue
		}

		switch env.Command {
		case "configurationDone":
			if err := p.clientW.Write(successResponse(env.Seq, env.Command, nil)); err != nil {
				return err
			}
			if err := p.clientW.Write(makeEvent("stopped", map[string]any{
				"reason":            "entry",
				"threadId":          1,
				"allThreadsStopped": true,
			})); err != nil {
				return err
			}
		case "setBreakpoints":
			body, err := p.handleSetBreakpoints(env.Arguments)
			if err != nil {
				return p.clientW.Write(errorResponse(env.Seq, env.Command, err.Error()))
			}
			if err := p.clientW.Write(successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "threads":
			body := json.RawMessage(`{"threads":[{"id":1,"name":"MainThread"}]}`)
			if err := p.clientW.Write(successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "continue", "next", "stepIn", "stepOut", "stepBack", "reverseContinue":
			if err := p.clientW.Write(successResponse(env.Seq, env.Command, json.RawMessage(`{"allThreadsContinued":true}`))); err != nil {
				return err
			}
			if err := p.runToNextStop(env.Command); err != nil {
				return p.clientW.Write(errorResponse(env.Seq, env.Command, err.Error()))
			}
		case "stackTrace":
			body := p.handleStackTrace()
			if err := p.clientW.Write(successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "scopes":
			body := p.handleScopes()
			if err := p.clientW.Write(successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "variables":
			body := p.handleVariables()
			if err := p.clientW.Write(successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "evaluate":
			body := json.RawMessage(`{"result":"<evaluation unavailable>","variablesReference":0}`)
			if err := p.clientW.Write(successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "source":
			body := p.handleSource(env.Arguments)
			if err := p.clientW.Write(successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "disconnect":
			if p.debugger != nil {
				_ = p.debugger.Close()
			}
			p.currentCursor = nil
			if err := p.clientW.Write(successResponse(env.Seq, env.Command, nil)); err != nil {
				return err
			}
			return nil
		default:
			if err := p.clientW.Write(errorResponse(env.Seq, env.Command, "request not supported by Go-only DAP proxy yet")); err != nil {
				return err
			}
		}
	}
}

func breakpointKey(spec BreakpointSpec) string {
	k := fmt.Sprintf("%s:%d", spec.File, spec.Line)
	if spec.Condition != "" {
		k += ":" + spec.Condition
	}
	return k
}

func (p *Proxy) handleSetBreakpoints(argsRaw json.RawMessage) (json.RawMessage, error) {
	var args setBreakpointsArgs
	if len(argsRaw) > 0 {
		if err := json.Unmarshal(argsRaw, &args); err != nil {
			return nil, fmt.Errorf("parse setBreakpoints args: %w", err)
		}
	}

	newSpecs := make(map[string]BreakpointSpec, len(args.Breakpoints))
	for _, bp := range args.Breakpoints {
		spec := BreakpointSpec{
			File:      args.Source.Path,
			Line:      bp.Line,
			Condition: bp.Condition,
		}
		newSpecs[breakpointKey(spec)] = spec
	}

	for key, id := range p.breakpointIDs {
		if _, ok := newSpecs[key]; !ok {
			p.debugger.RemoveBreakpoint(id)
			delete(p.breakpointIDs, key)
		}
	}

	ctx := context.Background()
	out := make([]map[string]any, 0, len(args.Breakpoints))
	for _, bp := range args.Breakpoints {
		spec := BreakpointSpec{
			File:      args.Source.Path,
			Line:      bp.Line,
			Condition: bp.Condition,
		}
		key := breakpointKey(spec)
		if _, exists := p.breakpointIDs[key]; !exists {
			log.Printf("setBreakpoints: adding %s:%d", args.Source.Path, bp.Line)
			id, err := p.debugger.AddBreakpoint(ctx, spec)
			if err != nil {
				log.Printf("setBreakpoints: AddBreakpoint error: %v", err)
				out = append(out, map[string]any{"verified": false, "line": bp.Line, "message": err.Error()})
				continue
			}
			log.Printf("setBreakpoints: registered id=%d, total breakpoints=%d", id, len(p.breakpointIDs)+1)
			p.breakpointIDs[key] = id
		}
		out = append(out, map[string]any{"verified": true, "line": bp.Line})
	}

	body, err := json.Marshal(map[string]any{"breakpoints": out})
	return body, err
}

func (p *Proxy) runToNextStop(command string) error {
	if p.debugger == nil {
		return fmt.Errorf("no active debugger")
	}

	ctx := context.Background()

	switch command {
	case "continue":
		return p.handleContinue(ctx, false)
	case "reverseContinue":
		return p.handleContinue(ctx, true)
	case "next":
		return p.handleCursorNav(ctx, "step", func() (*Cursor, error) {
			return p.currentCursor.Next(ctx)
		})
	case "stepIn":
		return p.handleCursorNav(ctx, "step", func() (*Cursor, error) {
			return p.currentCursor.StepInto(ctx)
		})
	case "stepOut":
		return p.handleCursorNav(ctx, "step", func() (*Cursor, error) {
			return p.currentCursor.Return(ctx)
		})
	case "stepBack":
		return p.handleCursorNav(ctx, "step", func() (*Cursor, error) {
			return p.currentCursor.Previous(ctx)
		})
	default:
		return fmt.Errorf("unhandled navigation command: %s", command)
	}
}

func (p *Proxy) handleContinue(ctx context.Context, reverse bool) error {
	if err := p.debugger.WaitForScans(ctx); err != nil {
		return fmt.Errorf("waiting for breakpoint scans: %w", err)
	}

	var hit BreakpointHit
	var ok bool
	hitsLen := p.debugger.Hits().Len()
	log.Printf("handleContinue: cursor=%v reverse=%v currentMsgIdx=%d hitsLen=%d",
		p.currentCursor != nil, reverse, p.currentMessageIndex, hitsLen)
	if p.currentCursor == nil {
		hit, ok = p.debugger.Hits().FirstFrom(0)
	} else if reverse {
		hit, ok = p.debugger.Hits().PrevBefore(p.currentMessageIndex)
	} else {
		hit, ok = p.debugger.Hits().NextAfter(p.currentMessageIndex)
	}
	log.Printf("handleContinue: ok=%v hitMsgIdx=%d", ok, hit.Location.MessageIndex)
	if !ok {
		return p.clientW.Write(makeEvent("terminated", map[string]any{}))
	}

	p.currentMessageIndex = hit.Location.MessageIndex
	snap, err := p.provider.ClosestBeforeCall(ctx, hit.Location.ThreadID, hit.Location.FunctionCounts)
	if err != nil {
		log.Printf("warning: failed to get snapshot: %v", err)
	} else {
		rp, err := snap.Replay(ctx)
		if err != nil {
			log.Printf("warning: failed to fork snapshot: %v", err)
		} else if _, err := rp.RunToCursor(ctx, hit.Location.RawCursor()); err != nil {
			log.Printf("warning: failed to materialise replay: %v", err)
			rp.Close()
		} else {
			p.currentCursor = NewCursor(rp.Location(), p.provider, rp)
		}
	}

	return p.clientW.Write(makeEvent("stopped", map[string]any{
		"reason":            "breakpoint",
		"threadId":          1,
		"allThreadsStopped": true,
	}))
}

func (p *Proxy) handleCursorNav(ctx context.Context, reason string, nav func() (*Cursor, error)) error {
	if p.currentCursor == nil {
		return p.clientW.Write(makeEvent("stopped", map[string]any{
			"reason":            reason,
			"threadId":          1,
			"allThreadsStopped": true,
		}))
	}

	loc := p.currentCursor.Location()
	log.Printf("handleCursorNav: reason=%s msgIdx=%d FLasti=%v", reason, loc.MessageIndex, loc.FLasti)

	next, err := nav()
	if err != nil {
		log.Printf("navigation failed: %v, falling back to HitList", err)
		hit, ok := p.debugger.Hits().NextAfter(p.currentMessageIndex)
		if !ok {
			return p.clientW.Write(makeEvent("terminated", map[string]any{}))
		}
		p.currentMessageIndex = hit.Location.MessageIndex
		snap, snapErr := p.provider.ClosestBeforeCall(ctx, hit.Location.ThreadID, hit.Location.FunctionCounts)
		if snapErr != nil {
			log.Printf("warning: failed to get snapshot: %v", snapErr)
		} else if rp, forkErr := snap.Replay(ctx); forkErr != nil {
			log.Printf("warning: failed to fork snapshot: %v", forkErr)
		} else if _, runErr := rp.RunToCursor(ctx, hit.Location.RawCursor()); runErr != nil {
			log.Printf("warning: failed to materialise replay: %v", runErr)
			rp.Close()
		} else {
			p.currentCursor = NewCursor(rp.Location(), p.provider, rp)
		}
	} else {
		nl := next.Location()
		log.Printf("handleCursorNav: advanced to msgIdx=%d FLasti=%v", nl.MessageIndex, nl.FLasti)
		p.currentMessageIndex = nl.MessageIndex
		p.currentCursor = next
	}

	return p.clientW.Write(makeEvent("stopped", map[string]any{
		"reason":            reason,
		"threadId":          1,
		"allThreadsStopped": true,
	}))
}

func (p *Proxy) handleStackTrace() json.RawMessage {
	if p.currentCursor == nil {
		return json.RawMessage(`{"stackFrames":[],"totalFrames":0}`)
	}
	ctx := context.Background()
	frames, err := p.currentCursor.Stack(ctx)
	if err != nil {
		log.Printf("stackTrace: %v", err)
		return json.RawMessage(`{"stackFrames":[],"totalFrames":0}`)
	}
	dapFrames := make([]map[string]any, 0, len(frames))
	for i, f := range frames {
		df := map[string]any{
			"id":     i,
			"name":   stringOr(f, "function", "<unknown>"),
			"line":   intOr(f, "line", 0),
			"column": 0,
		}
		if filename, ok := f["filename"].(string); ok && filename != "" {
			absPath := p.resolveSourcePath(filename)
			df["source"] = map[string]any{
				"name": filepath.Base(filename),
				"path": absPath,
			}
		}
		dapFrames = append(dapFrames, df)
	}
	body, _ := json.Marshal(map[string]any{
		"stackFrames": dapFrames,
		"totalFrames": len(dapFrames),
	})
	return body
}

func (p *Proxy) handleSource(args json.RawMessage) json.RawMessage {
	var a struct {
		Source struct {
			Path string `json:"path"`
		} `json:"source"`
	}
	if err := json.Unmarshal(args, &a); err != nil || a.Source.Path == "" {
		body, _ := json.Marshal(map[string]any{"content": "// source unavailable"})
		return body
	}
	path := p.resolveSourcePath(a.Source.Path)
	data, err := os.ReadFile(path)
	if err != nil {
		body, _ := json.Marshal(map[string]any{"content": fmt.Sprintf("// could not read %s: %v", path, err)})
		return body
	}
	body, _ := json.Marshal(map[string]any{"content": string(data), "mimeType": "text/x-python"})
	return body
}

// resolveSourcePath converts a potentially relative co_filename to an
// absolute path. It tries the recording directory first, then falls back
// to the process CWD.
func (p *Proxy) resolveSourcePath(filename string) string {
	if filepath.IsAbs(filename) {
		return filename
	}
	candidate := filepath.Join(p.recordingDir, filename)
	if _, err := os.Stat(candidate); err == nil {
		return candidate
	}
	if abs, err := filepath.Abs(filename); err == nil {
		return abs
	}
	return filename
}

func (p *Proxy) handleScopes() json.RawMessage {
	body, _ := json.Marshal(map[string]any{
		"scopes": []map[string]any{
			{"name": "Locals", "variablesReference": 1, "expensive": false},
		},
	})
	return body
}

func (p *Proxy) handleVariables() json.RawMessage {
	if p.currentCursor == nil {
		return json.RawMessage(`{"variables":[]}`)
	}
	ctx := context.Background()
	vars, err := p.currentCursor.Locals(ctx)
	if err != nil {
		log.Printf("variables: %v", err)
		return json.RawMessage(`{"variables":[]}`)
	}
	dapVars := make([]map[string]any, 0, len(vars))
	for _, v := range vars {
		dv := map[string]any{
			"name":               stringOr(v, "name", "?"),
			"value":              stringOr(v, "value", ""),
			"type":               stringOr(v, "type", ""),
			"variablesReference": 0,
		}
		dapVars = append(dapVars, dv)
	}
	body, _ := json.Marshal(map[string]any{"variables": dapVars})
	return body
}

func stringOr(m map[string]any, key, fallback string) string {
	if v, ok := m[key].(string); ok {
		return v
	}
	return fallback
}

func intOr(m map[string]any, key string, fallback int) int {
	if v, ok := m[key].(float64); ok {
		return int(v)
	}
	if v, ok := m[key].(int); ok {
		return v
	}
	return fallback
}

// --- message construction helpers ---

func successResponse(reqSeq int, command string, body json.RawMessage) json.RawMessage {
	m := map[string]any{
		"seq":         1,
		"type":        "response",
		"request_seq": reqSeq,
		"command":     command,
		"success":     true,
	}
	if body != nil {
		var b any
		_ = json.Unmarshal(body, &b)
		m["body"] = b
	}
	out, _ := json.Marshal(m)
	return out
}

func errorResponse(reqSeq int, command, message string) json.RawMessage {
	m := map[string]any{
		"seq":         1,
		"type":        "response",
		"request_seq": reqSeq,
		"command":     command,
		"success":     false,
		"message":     message,
		"body": map[string]any{
			"error": map[string]any{
				"id":     1,
				"format": message,
			},
		},
	}
	out, _ := json.Marshal(m)
	return out
}

func makeEvent(name string, body any) json.RawMessage {
	m := map[string]any{
		"seq":   1,
		"type":  "event",
		"event": name,
	}
	if body != nil {
		m["body"] = body
	}
	out, _ := json.Marshal(m)
	return out
}
