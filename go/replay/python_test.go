package replay

import (
	"os"
	"path/filepath"
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

func TestPythonCommandForTargetUsesRetraceVenvRealPythonWithWrapperEnv(t *testing.T) {
	target := runnerTarget{
		PythonBin: ".retrace-venv/bin/python",
		CWD:       "/tmp/project",
		Preamble: map[string]any{
			"env": map[string]any{
				"RETRACE_REAL_PYTHON":    ".retrace-venv/bin/.retrace-python-real",
				"RETRACE_PYTHON_WRAPPER": ".retrace-venv/bin/python",
				"PYTHONEXECUTABLE":       ".retrace-venv/bin/python",
			},
		},
	}

	cmd := pythonCommandForTarget(target, "-m", "retracesoftware")

	wantPath := filepath.Join(
		"/tmp/project",
		".retrace-venv/bin/.retrace-python-real",
	)
	if cmd.Path != wantPath {
		t.Fatalf("cmd.Path = %q, want %q", cmd.Path, wantPath)
	}
	if got := envValue(cmd.Env, "PYTHONEXECUTABLE"); got != ".retrace-venv/bin/python" {
		t.Fatalf("PYTHONEXECUTABLE = %q, want recorded wrapper", got)
	}
	if got := envValue(cmd.Env, "RETRACE_PYTHON_WRAPPER"); got != ".retrace-venv/bin/python" {
		t.Fatalf("RETRACE_PYTHON_WRAPPER = %q, want recorded wrapper", got)
	}
}

func TestPythonCommandForTargetKeepsRecordedPythonWithoutWrapperEnv(t *testing.T) {
	target := runnerTarget{
		PythonBin: "/usr/bin/python3",
		CWD:       "/tmp/project",
		Preamble: map[string]any{
			"env": map[string]any{
				"RETRACE_REAL_PYTHON": "/should/not/use",
			},
		},
	}

	cmd := pythonCommandForTarget(target, "-m", "retracesoftware")

	if cmd.Path != "/usr/bin/python3" {
		t.Fatalf("cmd.Path = %q, want recorded python", cmd.Path)
	}
}
