package replay

import (
	"encoding/json"
	"testing"
	"time"
)

func TestProxyScopesRejectUnknownFrameReference(t *testing.T) {
	proxy := &Proxy{currentCursor: &Cursor{}}

	var body struct {
		Scopes []struct {
			Name               string `json:"name"`
			VariablesReference int    `json:"variablesReference"`
		} `json:"scopes"`
	}
	args, _ := json.Marshal(map[string]any{"frameId": 999})
	if err := json.Unmarshal(proxy.handleScopes(args), &body); err != nil {
		t.Fatal(err)
	}

	if len(body.Scopes) != 0 {
		t.Fatalf("expected no scopes without a known frame reference, got %#v", body.Scopes)
	}
}

func TestDAPScopesVariablesAndEvaluateAreFrameBound(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping e2e test in short mode")
	}
	python := requirePython312(t)

	const source = `def inner():
    inner_local = "inside"
    stop_value = inner_local
    return stop_value

def outer():
    outer_local = "outside"
    return inner()

result = outer()
print(result)
`
	session := newDAPSession(t, python, "scope_reference_target.py", source)
	session.setBreakpoint(3)
	session.configurationDone()
	session.continueToBreakpoint()

	frames := dapStackFrames(t, session)
	if len(frames) < 2 {
		t.Fatalf("expected at least inner and outer frames, got %v", frames)
	}
	innerID := dapFrameID(t, frames[0])
	outerID := dapFrameID(t, frames[1])

	innerVars := dapVariablesForFrame(t, session, innerID)
	assertDAPVariableNames(t, innerVars, []string{"inner_local"}, []string{"outer_local"})

	outerVars := dapVariablesForFrame(t, session, outerID)
	assertDAPVariableNames(t, outerVars, []string{"outer_local"}, []string{"inner_local"})

	innerEval := dapEvaluate(t, session, innerID, "inner_local + '-eval'")
	if innerEval["result"] != "'inside-eval'" {
		t.Fatalf("inner evaluate result = %v, want 'inside-eval'; response: %v", innerEval["result"], innerEval)
	}

	outerEval := dapEvaluate(t, session, outerID, "outer_local + '-eval'")
	if outerEval["result"] != "'outside-eval'" {
		t.Fatalf("outer evaluate result = %v, want 'outside-eval'; response: %v", outerEval["result"], outerEval)
	}
}

func dapStackFrames(t *testing.T, session *dapSession) []any {
	t.Helper()
	session.client.send("stackTrace", map[string]any{"threadId": 1})
	resp := session.expectResponse("stackTrace", 10*time.Second)
	body, _ := resp["body"].(map[string]any)
	frames, _ := body["stackFrames"].([]any)
	if len(frames) == 0 {
		t.Fatalf("stackTrace returned no frames: %v", resp)
	}
	return frames
}

func dapFrameID(t *testing.T, raw any) int {
	t.Helper()
	frame, _ := raw.(map[string]any)
	id, ok := frame["id"].(float64)
	if !ok {
		t.Fatalf("frame has no numeric id: %v", frame)
	}
	return int(id)
}

func dapVariablesForFrame(t *testing.T, session *dapSession, frameID int) []map[string]any {
	t.Helper()
	session.client.send("scopes", map[string]any{"frameId": frameID})
	scopesResp := session.expectResponse("scopes", 10*time.Second)
	body, _ := scopesResp["body"].(map[string]any)
	scopes, _ := body["scopes"].([]any)
	if len(scopes) == 0 {
		t.Fatalf("scopes returned no scopes for frame %d: %v", frameID, scopesResp)
	}
	firstScope, _ := scopes[0].(map[string]any)
	ref, ok := firstScope["variablesReference"].(float64)
	if !ok || ref == 0 {
		t.Fatalf("scope has no variablesReference: %v", firstScope)
	}

	session.client.send("variables", map[string]any{"variablesReference": int(ref)})
	varsResp := session.expectResponse("variables", 10*time.Second)
	body, _ = varsResp["body"].(map[string]any)
	rawVars, _ := body["variables"].([]any)
	vars := make([]map[string]any, 0, len(rawVars))
	for _, raw := range rawVars {
		if v, ok := raw.(map[string]any); ok {
			vars = append(vars, v)
		}
	}
	return vars
}

func assertDAPVariableNames(t *testing.T, vars []map[string]any, wantPresent, wantAbsent []string) {
	t.Helper()
	names := make(map[string]bool, len(vars))
	for _, v := range vars {
		name, _ := v["name"].(string)
		names[name] = true
	}
	for _, name := range wantPresent {
		if !names[name] {
			t.Fatalf("expected variable %q in %v", name, names)
		}
	}
	for _, name := range wantAbsent {
		if names[name] {
			t.Fatalf("did not expect stale variable %q in %v", name, names)
		}
	}
}

func dapEvaluate(t *testing.T, session *dapSession, frameID int, expression string) map[string]any {
	t.Helper()
	session.client.send("evaluate", map[string]any{
		"expression": expression,
		"frameId":    frameID,
		"context":    "repl",
	})
	resp := session.expectResponse("evaluate", 10*time.Second)
	body, _ := resp["body"].(map[string]any)
	return body
}
