package replay

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"sync/atomic"
	"time"
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

const defaultNavigationTimeout = 30 * time.Second

// Proxy is the Go-owned DAP endpoint that maps requests to Debugger calls.
type Proxy struct {
	pidFile      string
	recordingDir string // directory containing the recording/extracted pidfiles
	processCWD   string // cwd recorded in the process preamble
	root         *Replay
	debugger     Debugger
	provider     SnapshotProvider
	clientR      *bufio.Reader
	clientW      *Writer
	launchErr    error

	breakpointIDs        map[string]int // "file:line[:cond]" -> debugger breakpoint ID
	breakpointSpecs      map[string]BreakpointSpec
	exceptionFilters     map[string]bool
	currentException     *ExceptionInfo
	currentExceptionMode string
	currentMessageIndex  uint64
	currentCursor        *Cursor // current position, nil until first navigation
	currentHit           BreakpointHit
	hasCurrentHit        bool
	navHistory           []*Cursor
	navigatedFromHit     bool // true after step/nav, false after continue lands on a hit
	navTimeout           time.Duration
	seq                  atomic.Int64
}

func NewProxy(pidFile string, clientIn io.Reader, clientW *Writer) *Proxy {
	// pidFile is <recording>.d/<pid>.bin — parent of parent is the recording dir
	recDir := filepath.Dir(filepath.Dir(pidFile))
	processCWD := ""
	if pidFile != "" {
		if process, err := ReadProcess(pidFile); err == nil {
			processCWD, _ = process["cwd"].(string)
		} else {
			log.Printf("warning: could not read pidfile cwd: %v", err)
		}
	}
	return &Proxy{
		pidFile:          pidFile,
		recordingDir:     recDir,
		processCWD:       processCWD,
		clientR:          bufio.NewReader(clientIn),
		clientW:          clientW,
		breakpointIDs:    make(map[string]int),
		breakpointSpecs:  make(map[string]BreakpointSpec),
		exceptionFilters: make(map[string]bool),
		navTimeout:       defaultNavigationTimeout,
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
	log.Printf("protocol debug logging: %v (RETRACE_DEBUG_PROTOCOL=%q)",
		debugProtocol, os.Getenv("RETRACE_DEBUG_PROTOCOL"))
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
			resp := p.errorResponse(env.Seq, env.Command,
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
		"supportsConditionalBreakpoints": true,
		"supportsExceptionInfoRequest": true,
		"supportsStepBack": true,
		"supportsStepInTargetsRequest": false,
		"supportsSteppingGranularity": true,
		"exceptionBreakpointFilters": [
			{"filter": "raised", "label": "Raised Exceptions", "default": true},
			{"filter": "uncaught", "label": "Uncaught Exceptions"}
		]
	}`)

	resp := p.successResponse(env.Seq, "initialize", caps)
	if err := p.clientW.Write(resp); err != nil {
		return err
	}
	return p.clientW.Write(p.makeEvent("initialized", nil))
}

func (p *Proxy) handleLaunch(env envelope) error {
	if p.launchErr != nil {
		return p.clientW.Write(p.errorResponse(env.Seq, "launch", p.launchErr.Error()))
	}
	if p.pidFile == "" {
		return p.clientW.Write(p.errorResponse(env.Seq, "launch", "no pidFile configured"))
	}
	log.Printf("launch: pidFile=%s", p.pidFile)

	if p.debugger == nil {
		ctx := context.Background()
		dapLog := NewDAPLogWriter(p.clientW)
		root, err := StartReplayFromPidFile(ctx, p.pidFile, dapLog, dapLog)
		if err != nil {
			return p.clientW.Write(p.errorResponse(env.Seq, "launch", err.Error()))
		}
		p.root = root
		engine := NewQueryEngine(root, p.pidFile, dapLog)
		p.debugger = NewDebugger(engine)
		p.provider = engine.Provider()
	}

	return p.clientW.Write(p.successResponse(env.Seq, "launch", nil))
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
			if err := p.clientW.Write(p.successResponse(env.Seq, env.Command, nil)); err != nil {
				return err
			}
			if err := p.clientW.Write(p.makeEvent("stopped", map[string]any{
				"reason":            "entry",
				"threadId":          1,
				"allThreadsStopped": true,
			})); err != nil {
				return err
			}
		case "setBreakpoints":
			body, err := p.handleSetBreakpoints(env.Arguments)
			if err != nil {
				return p.clientW.Write(p.errorResponse(env.Seq, env.Command, err.Error()))
			}
			if err := p.clientW.Write(p.successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "setExceptionBreakpoints":
			body, err := p.handleSetExceptionBreakpoints(env.Arguments)
			if err != nil {
				return p.clientW.Write(p.errorResponse(env.Seq, env.Command, err.Error()))
			}
			if err := p.clientW.Write(p.successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "threads":
			body := json.RawMessage(`{"threads":[{"id":1,"name":"MainThread"}]}`)
			if err := p.clientW.Write(p.successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "continue", "next", "stepIn", "stepOut", "stepBack", "reverseContinue":
			if err := p.clientW.Write(p.successResponse(env.Seq, env.Command, json.RawMessage(`{"allThreadsContinued":true}`))); err != nil {
				return err
			}
			if err := p.runToNextStop(env.Command); err != nil {
				return p.clientW.Write(p.errorResponse(env.Seq, env.Command, err.Error()))
			}
		case "stackTrace":
			body := p.handleStackTrace()
			if err := p.clientW.Write(p.successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "scopes":
			body := p.handleScopes()
			if err := p.clientW.Write(p.successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "variables":
			body := p.handleVariables()
			if err := p.clientW.Write(p.successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "evaluate":
			body := json.RawMessage(`{"result":"<evaluation unavailable>","variablesReference":0}`)
			if err := p.clientW.Write(p.successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "exceptionInfo":
			body := p.handleExceptionInfo()
			if err := p.clientW.Write(p.successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "source":
			body := p.handleSource(env.Arguments)
			if err := p.clientW.Write(p.successResponse(env.Seq, env.Command, body)); err != nil {
				return err
			}
		case "disconnect":
			if p.debugger != nil {
				_ = p.debugger.Close()
			}
			p.currentCursor = nil
			p.hasCurrentHit = false
			if err := p.clientW.Write(p.successResponse(env.Seq, env.Command, nil)); err != nil {
				return err
			}
			return nil
		default:
			if err := p.clientW.Write(p.errorResponse(env.Seq, env.Command, "request not supported by Go-only DAP proxy yet")); err != nil {
				return err
			}
		}
	}
}

func flasti(p *int) string {
	if p == nil {
		return "<nil>"
	}
	return fmt.Sprintf("%d", *p)
}

func breakpointKey(spec BreakpointSpec) string {
	k := fmt.Sprintf("%s:%d", spec.File, spec.Line)
	if spec.Condition != "" {
		k += ":" + spec.Condition
	}
	return k
}

func (p *Proxy) handleSetBreakpoints(argsRaw json.RawMessage) (json.RawMessage, error) {
	if p.debugger == nil {
		return nil, fmt.Errorf("no active debugger")
	}
	if p.breakpointSpecs == nil {
		p.breakpointSpecs = make(map[string]BreakpointSpec)
	}

	var args setBreakpointsArgs
	if len(argsRaw) > 0 {
		if err := json.Unmarshal(argsRaw, &args); err != nil {
			return nil, fmt.Errorf("parse setBreakpoints args: %w", err)
		}
	}

	sourcePath := p.resolveSourcePath(args.Source.Path)
	newSpecs := make(map[string]BreakpointSpec, len(args.Breakpoints))
	for _, bp := range args.Breakpoints {
		spec := BreakpointSpec{
			File:      sourcePath,
			Line:      bp.Line,
			Condition: bp.Condition,
		}
		newSpecs[breakpointKey(spec)] = spec
	}

	for key, id := range p.breakpointIDs {
		spec, ok := p.breakpointSpecs[key]
		if ok && spec.File != sourcePath {
			continue
		}
		if _, ok := newSpecs[key]; !ok {
			p.debugger.RemoveBreakpoint(id)
			delete(p.breakpointIDs, key)
			delete(p.breakpointSpecs, key)
		}
	}

	ctx := context.Background()
	out := make([]map[string]any, 0, len(args.Breakpoints))
	for _, bp := range args.Breakpoints {
		if !sourceLineCanBreak(sourcePath, bp.Line) {
			out = append(out, map[string]any{
				"verified": false,
				"line":     bp.Line,
				"message":  "source line is not executable",
			})
			continue
		}
		spec := BreakpointSpec{
			File:      sourcePath,
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
		p.breakpointSpecs[key] = spec
		out = append(out, map[string]any{"verified": true, "line": bp.Line})
	}

	body, err := json.Marshal(map[string]any{"breakpoints": out})
	return body, err
}

func (p *Proxy) handleSetExceptionBreakpoints(argsRaw json.RawMessage) (json.RawMessage, error) {
	var args struct {
		Filters []string `json:"filters"`
	}
	if len(argsRaw) > 0 {
		if err := json.Unmarshal(argsRaw, &args); err != nil {
			return nil, fmt.Errorf("parse setExceptionBreakpoints args: %w", err)
		}
	}
	p.exceptionFilters = make(map[string]bool, len(args.Filters))
	for _, filter := range args.Filters {
		switch filter {
		case "raised", "uncaught":
			p.exceptionFilters[filter] = true
		}
	}
	return json.RawMessage(`{"breakpoints":[]}`), nil
}

func (p *Proxy) exceptionsEnabled() bool {
	return p.exceptionFilters["raised"] || p.exceptionFilters["uncaught"]
}

func sourceLineCanBreak(path string, line int) bool {
	if path == "" || line <= 0 {
		return false
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	lines := bytes.Split(data, []byte("\n"))
	if line > len(lines) {
		return false
	}
	trimmed := bytes.TrimSpace(lines[line-1])
	return len(trimmed) > 0 && !bytes.HasPrefix(trimmed, []byte("#"))
}

func (p *Proxy) runToNextStop(command string) error {
	if p.debugger == nil {
		return fmt.Errorf("no active debugger")
	}

	ctx, cancel := context.WithTimeout(context.Background(), p.navTimeout)
	defer cancel()

	switch command {
	case "continue":
		return p.handleContinue(ctx, false)
	case "reverseContinue":
		return p.handleContinue(ctx, true)
	case "next":
		return p.handleCursorNav(ctx, "step", func() (*Cursor, error) {
			return p.currentCursor.Next(ctx)
		}, true)
	case "stepIn":
		return p.handleCursorNav(ctx, "step", func() (*Cursor, error) {
			return p.currentCursor.StepInto(ctx)
		}, true)
	case "stepOut":
		return p.handleCursorNav(ctx, "step", func() (*Cursor, error) {
			return p.currentCursor.Return(ctx)
		}, true)
	case "stepBack":
		return p.handleCursorNav(ctx, "step", func() (*Cursor, error) {
			if n := len(p.navHistory); n > 0 {
				prev := p.navHistory[n-1]
				p.navHistory = p.navHistory[:n-1]
				return prev, nil
			}
			return p.currentCursor.PreviousStatement(ctx)
		}, false)
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
	if !reverse && p.exceptionsEnabled() && hitsLen == 0 {
		return p.handleContinueToException(ctx)
	}
	if p.currentCursor == nil {
		hit, ok = p.debugger.Hits().FirstFrom(0)
	} else if reverse {
		if p.hasCurrentHit && !p.navigatedFromHit {
			hit, ok = p.debugger.Hits().PrevBeforeHit(p.currentHit)
		} else {
			hit, ok = p.debugger.Hits().LastAtOrBeforeLocation(p.currentCursor.Location())
		}
	} else if p.hasCurrentHit && !p.navigatedFromHit {
		hit, ok = p.debugger.Hits().NextAfterHit(p.currentHit)
	} else {
		hit, ok = p.debugger.Hits().NextAfterLocation(p.currentCursor.Location())
	}
	log.Printf("handleContinue: ok=%v hitMsgIdx=%d", ok, hit.Location.MessageIndex)
	if !ok {
		return p.clientW.Write(p.makeEvent("terminated", map[string]any{}))
	}

	p.currentMessageIndex = hit.Location.MessageIndex
	p.currentHit = hit
	p.hasCurrentHit = true
	p.currentException = nil
	p.currentExceptionMode = ""
	p.navigatedFromHit = false
	p.navHistory = nil
	p.currentCursor = NewCursor(hit.Location, p.provider, nil)
	snap, err := p.provider.ClosestBeforeCall(ctx, hit.Location.ThreadID, hit.Location.FunctionCounts)
	if err != nil {
		log.Printf("warning: failed to get snapshot: %v", err)
	} else {
		rp, err := snap.Replay(ctx)
		if err != nil {
			log.Printf("warning: failed to fork snapshot: %v", err)
		} else {
			loc, err := rp.FindFirstBreakpoint(ctx, hit.Spec.ToMap())
			if err != nil {
				log.Printf("warning: failed to materialise breakpoint: %v", err)
				rp.Close()
			} else if loc == nil {
				log.Printf("warning: failed to materialise breakpoint: hit not found")
				rp.Close()
			} else {
				p.currentCursor = NewCursor(*loc, p.provider, rp)
			}
		}
	}

	return p.clientW.Write(p.makeEvent("stopped", map[string]any{
		"reason":            "breakpoint",
		"threadId":          1,
		"allThreadsStopped": true,
	}))
}

func (p *Proxy) handleContinueToException(ctx context.Context) error {
	if p.root == nil {
		return fmt.Errorf("no active replay root")
	}

	result, err := p.root.StopAtFailure(ctx)
	if err != nil {
		return err
	}
	if result.Reason != "exception" {
		return p.clientW.Write(p.makeEvent("terminated", map[string]any{}))
	}

	loc := locationFromStopResult(result)
	p.currentMessageIndex = loc.MessageIndex
	p.currentCursor = NewCursor(loc, p.provider, p.root)
	p.currentException = result.Exception
	p.currentExceptionMode = "always"
	if p.exceptionFilters["uncaught"] && !p.exceptionFilters["raised"] {
		// The current replay runtime stops at raised application exceptions.
		// Keep the requested mode for DAP exceptionInfo while using the same
		// capture mechanism.
		p.currentExceptionMode = "unhandled"
	}
	p.hasCurrentHit = false
	p.navigatedFromHit = false
	p.navHistory = nil

	body := map[string]any{
		"reason":            "exception",
		"threadId":          1,
		"allThreadsStopped": true,
	}
	if p.currentException != nil {
		body["text"] = p.currentException.Type
		body["description"] = p.currentException.Message
	}
	return p.clientW.Write(p.makeEvent("stopped", body))
}

func (p *Proxy) handleCursorNav(ctx context.Context, reason string, nav func() (*Cursor, error), recordHistory bool) (retErr error) {
	defer func() {
		if r := recover(); r != nil {
			log.Printf("PANIC in handleCursorNav (%s): %v", reason, r)
			retErr = fmt.Errorf("panic during %s: %v", reason, r)
		}
	}()

	if p.currentCursor == nil {
		return p.clientW.Write(p.makeEvent("stopped", map[string]any{
			"reason":            reason,
			"threadId":          1,
			"allThreadsStopped": true,
		}))
	}

	loc := p.currentCursor.Location()
	log.Printf("handleCursorNav: reason=%s msgIdx=%d flasti=%s fc=%v",
		reason, loc.MessageIndex, flasti(loc.FLasti), loc.FunctionCounts)

	var previous *Cursor
	if recordHistory {
		previous = NewCursor(loc, p.provider, nil)
	}
	next, err := nav()
	if err != nil {
		log.Printf("navigation failed (%s): %v, staying at current position", reason, err)
	} else {
		nl := next.Location()
		log.Printf("handleCursorNav: advanced to msgIdx=%d flasti=%s fc=%v",
			nl.MessageIndex, flasti(nl.FLasti), nl.FunctionCounts)
		if previous != nil && !previous.Location().Equal(nl) {
			p.navHistory = append(p.navHistory, previous)
		}
		p.currentCursor = next
		p.currentMessageIndex = nl.MessageIndex
		p.hasCurrentHit = false
		p.navigatedFromHit = true
	}

	return p.clientW.Write(p.makeEvent("stopped", map[string]any{
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

func (p *Proxy) handleExceptionInfo() json.RawMessage {
	if p.currentException == nil {
		body, _ := json.Marshal(map[string]any{
			"exceptionId": "<none>",
			"description": "No exception is associated with the current stop.",
			"breakMode":   "never",
		})
		return body
	}

	exceptionID := p.currentException.Type
	if exceptionID == "" {
		exceptionID = "<unknown>"
	}
	breakMode := p.currentExceptionMode
	if breakMode == "" {
		breakMode = "always"
	}
	body, _ := json.Marshal(map[string]any{
		"exceptionId": exceptionID,
		"description": p.currentException.Message,
		"breakMode":   breakMode,
		"details": map[string]any{
			"typeName": p.currentException.Type,
			"message":  p.currentException.Message,
		},
	})
	return body
}

// resolveSourcePath converts a potentially relative co_filename to an
// absolute path. Relative filenames are authored by the target process,
// so the recorded process cwd is the canonical base.
func (p *Proxy) resolveSourcePath(filename string) string {
	if filepath.IsAbs(filename) {
		return filename
	}
	var processCandidate string
	if p.processCWD != "" {
		processCandidate = filepath.Join(p.processCWD, filename)
		if _, err := os.Stat(processCandidate); err == nil {
			return processCandidate
		}
	}
	if p.recordingDir != "" {
		candidate := filepath.Join(p.recordingDir, filename)
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
	}
	if processCandidate != "" {
		return processCandidate
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

func (p *Proxy) nextSeq() int {
	return int(p.seq.Add(1))
}

func (p *Proxy) successResponse(reqSeq int, command string, body json.RawMessage) json.RawMessage {
	m := map[string]any{
		"seq":         p.nextSeq(),
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

func (p *Proxy) errorResponse(reqSeq int, command, message string) json.RawMessage {
	m := map[string]any{
		"seq":         p.nextSeq(),
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

func (p *Proxy) makeEvent(name string, body any) json.RawMessage {
	m := map[string]any{
		"seq":   p.nextSeq(),
		"type":  "event",
		"event": name,
	}
	if body != nil {
		m["body"] = body
	}
	out, _ := json.Marshal(m)
	return out
}
