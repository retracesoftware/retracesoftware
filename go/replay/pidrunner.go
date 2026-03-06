package replay

import (
	"encoding/json"
	"fmt"
)

// runnerTarget contains the resolved runtime metadata for a single PID stream.
type runnerTarget struct {
	Recording string
	PythonBin string
	CWD       string
	Preamble  map[string]any
}

type BreakpointSpec struct {
	File      string
	Line      int
	Condition string
}

func (b BreakpointSpec) ToMap() map[string]any {
	m := map[string]any{"file": b.File, "line": b.Line}
	if b.Condition != "" {
		m["condition"] = b.Condition
	}
	return m
}

func (b BreakpointSpec) Arg() (string, error) {
	if b.File == "" {
		return "", fmt.Errorf("breakpoint file is required")
	}
	if b.Line <= 0 {
		return "", fmt.Errorf("breakpoint line must be > 0")
	}
	if b.Condition != "" {
		return fmt.Sprintf("%s:%d:%s", b.File, b.Line, b.Condition), nil
	}
	return fmt.Sprintf("%s:%d", b.File, b.Line), nil
}

// BreakpointHit pairs a Debugger-assigned breakpoint ID with the Location
// where the breakpoint was hit.
type BreakpointHit struct {
	BreakpointID int
	Location     Location
}

type StackFrame struct {
	Name string `json:"name"`
	File string `json:"file"`
	Line int    `json:"line"`
}

// parseLocationFromPayload extracts a Location from a protocol event
// payload such as a breakpoint_hit event.
func parseLocationFromPayload(payload map[string]any) Location {
	rc := parseRawCursor(payload["cursor"])
	var msgIdx uint64
	if mi, ok := payload["message_index"].(float64); ok {
		msgIdx = uint64(mi)
	}
	return Location{
		ThreadID:       rc.ThreadID,
		FunctionCounts: rc.FunctionCounts,
		FLasti:         rc.FLasti,
		MessageIndex:   msgIdx,
	}
}

// parseLocationFromJSON parses a Location from raw JSON containing a
// "cursor" dict field.
func parseLocationFromJSON(line []byte) (Location, error) {
	var raw struct {
		Cursor RawCursor `json:"cursor"`
	}
	if err := json.Unmarshal(line, &raw); err != nil {
		return Location{}, fmt.Errorf("invalid cursor JSON: %w", err)
	}
	return Location{
		ThreadID:       raw.Cursor.ThreadID,
		FunctionCounts: raw.Cursor.FunctionCounts,
		FLasti:         raw.Cursor.FLasti,
	}, nil
}

// targetFromProcess builds a runner target from a resolved process metadata map.
func targetFromProcess(process map[string]any) (runnerTarget, error) {
	pythonBin, _ := process["executable"].(string)
	if pythonBin == "" {
		return runnerTarget{}, fmt.Errorf("process dict missing 'executable'")
	}
	cwd, _ := process["cwd"].(string)
	recording, _ := process["recording"].(string)
	if recording == "" {
		return runnerTarget{}, fmt.Errorf("process dict missing 'recording'")
	}
	return runnerTarget{
		Recording: recording,
		PythonBin: pythonBin,
		CWD:       cwd,
		Preamble:  process,
	}, nil
}
