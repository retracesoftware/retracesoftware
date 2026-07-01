package replay

import (
	"encoding/json"
	"fmt"
)

type ControlRequest struct {
	ID     string         `json:"id"`
	Type   string         `json:"type,omitempty"`
	Method string         `json:"method"`
	Params map[string]any `json:"params,omitempty"`
}

type ControlError struct {
	Code    string         `json:"code"`
	Message string         `json:"message"`
	Data    map[string]any `json:"data,omitempty"`
}

// ControlRequestError is returned when the Python control runtime rejects a
// request. It preserves the control error code so DAP callers can categorize
// failures instead of treating them as empty successful responses.
type ControlRequestError struct {
	Method  string
	Code    string
	Message string
}

func (e *ControlRequestError) Error() string {
	if e.Method != "" {
		return fmt.Sprintf("%s: %s: %s", e.Method, e.Code, e.Message)
	}
	return fmt.Sprintf("%s: %s", e.Code, e.Message)
}

type ControlResponse struct {
	ID     string         `json:"id"`
	Type   string         `json:"type"`
	OK     bool           `json:"ok"`
	Result map[string]any `json:"result,omitempty"`
	Error  *ControlError  `json:"error,omitempty"`
}

type ControlEvent struct {
	Type    string         `json:"type"`
	Event   string         `json:"event"`
	Payload map[string]any `json:"payload,omitempty"`
}

type ControlMessage struct {
	ID      string         `json:"id,omitempty"`
	Type    string         `json:"type,omitempty"`
	Kind    string         `json:"kind,omitempty"`
	Method  string         `json:"method,omitempty"`
	Event   string         `json:"event,omitempty"`
	OK      bool           `json:"ok,omitempty"`
	Result  map[string]any `json:"result,omitempty"`
	Error   *ControlError  `json:"error,omitempty"`
	Params  map[string]any `json:"params,omitempty"`
	Payload map[string]any `json:"payload,omitempty"`
}

func parseControlMessage(line []byte) (ControlMessage, error) {
	var msg ControlMessage
	err := json.Unmarshal(line, &msg)
	return msg, err
}

// RawCursor is the protocol-level cursor representation: a dict with
// thread_id, function_counts, and an optional f_lasti.
type RawCursor struct {
	ThreadID       uint64         `json:"thread_id"`
	FunctionCounts FunctionCounts `json:"function_counts"`
	FLasti         *int           `json:"f_lasti,omitempty"`
	Lineno         int            `json:"lineno,omitempty"`
}

// ToMap converts RawCursor to map[string]any for embedding in protocol messages.
func (rc RawCursor) ToMap() map[string]any {
	m := map[string]any{
		"thread_id":       rc.ThreadID,
		"function_counts": rc.FunctionCounts,
	}
	if rc.FLasti != nil {
		m["f_lasti"] = *rc.FLasti
	}
	return m
}

// ControlStopResult holds the raw protocol data from a stop message.
type ControlStopResult struct {
	Reason        string    `json:"reason"`
	MessageIndex  uint64    `json:"message_index"`
	Cursor        RawCursor `json:"cursor"`
	ThreadCursors map[uint64]RawCursor
	Exception     *ExceptionInfo `json:"exception,omitempty"`
	Location      *StopLocation  `json:"location,omitempty"`
	AppConfidence string         `json:"application_frame_confidence,omitempty"`
}

type ExceptionInfo struct {
	Type          string `json:"type,omitempty"`
	Message       string `json:"message,omitempty"`
	AssertionText string `json:"assertion_text,omitempty"`
	ControlFlow   bool   `json:"control_flow,omitempty"`
}

type StopLocation struct {
	Filename string `json:"filename,omitempty"`
	Line     int    `json:"line,omitempty"`
	Function string `json:"function,omitempty"`
}

func parseRawCursor(v any) RawCursor {
	m, ok := v.(map[string]any)
	if !ok {
		return RawCursor{}
	}
	var rc RawCursor
	if tid, ok := m["thread_id"].(float64); ok {
		rc.ThreadID = uint64(tid)
	}
	if raw, ok := m["function_counts"].([]any); ok {
		rc.FunctionCounts = make(FunctionCounts, 0, len(raw))
		for _, item := range raw {
			if n, ok := item.(float64); ok {
				rc.FunctionCounts = append(rc.FunctionCounts, int(n))
			}
		}
	}
	if fl, ok := m["f_lasti"].(float64); ok {
		v := int(fl)
		rc.FLasti = &v
	}
	if ln, ok := m["lineno"].(float64); ok {
		rc.Lineno = int(ln)
	}
	return rc
}

func parseStopResult(result map[string]any) ControlStopResult {
	out := ControlStopResult{
		Reason:        "idle",
		ThreadCursors: map[uint64]RawCursor{},
	}
	if v, ok := result["reason"].(string); ok && v != "" {
		out.Reason = v
	}
	if v, ok := result["message_index"].(float64); ok {
		out.MessageIndex = uint64(v)
	}
	out.Cursor = parseRawCursor(result["cursor"])
	if raw, ok := result["thread_cursors"].(map[string]any); ok {
		for k, v := range raw {
			var tid uint64
			if _, err := fmt.Sscanf(k, "%d", &tid); err != nil {
				continue
			}
			out.ThreadCursors[tid] = parseRawCursor(v)
		}
	}
	out.Exception = parseExceptionInfo(result["exception"])
	out.Location = parseStopLocation(result["location"])
	if v, ok := result["application_frame_confidence"].(string); ok {
		out.AppConfidence = v
	}
	return out
}

func parseExceptionInfo(v any) *ExceptionInfo {
	m, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	info := &ExceptionInfo{}
	if value, ok := m["type"].(string); ok {
		info.Type = value
	}
	if value, ok := m["message"].(string); ok {
		info.Message = value
	}
	if value, ok := m["assertion_text"].(string); ok {
		info.AssertionText = value
	}
	if value, ok := m["control_flow"].(bool); ok {
		info.ControlFlow = value
	}
	return info
}

func parseStopLocation(v any) *StopLocation {
	m, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	loc := &StopLocation{}
	if value, ok := m["filename"].(string); ok {
		loc.Filename = value
	}
	if value, ok := m["line"].(float64); ok {
		loc.Line = int(value)
	}
	if value, ok := m["function"].(string); ok {
		loc.Function = value
	}
	return loc
}
