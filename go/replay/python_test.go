package replay

import (
	"os"
	"strings"
	"testing"
)

func TestPythonEnvPreservesExistingPythonPathOrder(t *testing.T) {
	existing := "/recorded/build/cp311d/cpp/stream" + string(os.PathListSeparator) + "/recorded/src"
	env := pythonEnv([]string{"PYTHONPATH=" + existing, "MESONPY_EDITABLE_SKIP=/recorded/build/cp311d"})

	got := envValue(env, "PYTHONPATH")
	if !strings.HasPrefix(got, existing) {
		t.Fatalf("PYTHONPATH = %q, want recorded value first", got)
	}
	if !strings.Contains(got, string(os.PathListSeparator)) {
		t.Fatalf("PYTHONPATH = %q, want checkout paths appended", got)
	}

	gotSkip := envValue(env, "MESONPY_EDITABLE_SKIP")
	if !strings.HasPrefix(gotSkip, "/recorded/build/cp311d") {
		t.Fatalf("MESONPY_EDITABLE_SKIP = %q, want recorded value first", gotSkip)
	}
}

func TestPythonEnvForTargetUsesRecordedEnv(t *testing.T) {
	target := runnerTarget{
		PythonBin: "/usr/bin/python3",
		Preamble: map[string]any{
			"env": map[string]any{
				"PYTHONPATH":    "/recorded/src",
				"RETRACE_DEBUG": "1",
			},
		},
	}

	env := pythonEnvForTarget(target, []string{"PYTHONPATH=/fallback/src"})

	if got := envValue(env, "RETRACE_DEBUG"); got != "1" {
		t.Fatalf("RETRACE_DEBUG = %q, want recorded env", got)
	}
	if got := envValue(env, "PYTHONPATH"); got != "/recorded/src" {
		t.Fatalf("PYTHONPATH = %q, want recorded env without fallback/checkout paths", got)
	}
}
