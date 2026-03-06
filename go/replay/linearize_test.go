package replay

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLinearizeSingleProcess(t *testing.T) {
	preamble := jsonPreamble(map[string]any{
		"type":       "exec",
		"executable": "/usr/bin/python3",
	})
	data := []byte("trace-data-here")

	var trace []byte
	trace = append(trace, makePIDFrame(100, preamble)...)
	trace = append(trace, makePIDFrame(100, data)...)

	tmp := t.TempDir()
	tracePath := filepath.Join(tmp, "trace.retrace")
	os.WriteFile(tracePath, trace, 0644)

	idx, err := IndexTrace(tracePath)
	if err != nil {
		t.Fatal(err)
	}

	outDir := filepath.Join(tmp, "linear")
	os.MkdirAll(outDir, 0755)

	files, err := Linearize(idx, outDir)
	if err != nil {
		t.Fatal(err)
	}

	if len(files) != 1 {
		t.Fatalf("expected 1 file, got %d", len(files))
	}

	got, _ := os.ReadFile(files[0])
	expected := append(preamble, data...)
	if string(got) != string(expected) {
		t.Fatalf("content mismatch:\ngot:    %q\nexpect: %q", got, expected)
	}
}

func TestLinearizeWithForks(t *testing.T) {
	rootData1 := []byte("root-before-fork1-")
	rootData2 := []byte("root-between-forks-")
	rootData3 := []byte("root-after-all-forks")
	child1Data := []byte("child1-data")
	child2Data := []byte("child2-data")

	rootPreamble := jsonPreamble(map[string]any{
		"type":       "exec",
		"executable": "/usr/bin/python3",
	})

	// parent_offset for child1 = len(rootPreamble) + len(rootData1)
	child1ParentOffset := int64(len(rootPreamble) + len(rootData1))
	child1Preamble := jsonPreamble(map[string]any{
		"type":          "fork",
		"parent_pid":    100,
		"fork_index":    0,
		"parent_offset": child1ParentOffset,
	})

	// parent_offset for child2 = len(rootPreamble) + len(rootData1) + len(rootData2)
	child2ParentOffset := int64(len(rootPreamble) + len(rootData1) + len(rootData2))
	child2Preamble := jsonPreamble(map[string]any{
		"type":          "fork",
		"parent_pid":    100,
		"fork_index":    1,
		"parent_offset": child2ParentOffset,
	})

	// Build PID-framed trace with interleaved frames.
	var trace []byte
	trace = append(trace, makePIDFrame(100, rootPreamble)...)
	trace = append(trace, makePIDFrame(100, rootData1)...)
	trace = append(trace, makePIDFrame(200, child1Preamble)...)
	trace = append(trace, makePIDFrame(200, child1Data)...)
	trace = append(trace, makePIDFrame(100, rootData2)...)
	trace = append(trace, makePIDFrame(201, child2Preamble)...)
	trace = append(trace, makePIDFrame(201, child2Data)...)
	trace = append(trace, makePIDFrame(100, rootData3)...)

	tmp := t.TempDir()
	tracePath := filepath.Join(tmp, "trace.retrace")
	os.WriteFile(tracePath, trace, 0644)

	idx, err := IndexTrace(tracePath)
	if err != nil {
		t.Fatal(err)
	}

	outDir := filepath.Join(tmp, "linear")
	os.MkdirAll(outDir, 0755)

	files, err := Linearize(idx, outDir)
	if err != nil {
		t.Fatal(err)
	}

	if len(files) != 2 {
		t.Fatalf("expected 2 files, got %d: %v", len(files), files)
	}

	// Child 1: root preamble + rootData1 + child1Data
	f1, _ := os.ReadFile(filepath.Join(outDir, "200.bin"))
	expected1 := string(rootPreamble) + string(rootData1) + string(child1Data)
	if string(f1) != expected1 {
		t.Fatalf("child1 mismatch:\ngot:    %q\nexpect: %q", f1, expected1)
	}

	// Child 2: root preamble + rootData1 + rootData2 + child2Data
	f2, _ := os.ReadFile(filepath.Join(outDir, "201.bin"))
	expected2 := string(rootPreamble) + string(rootData1) + string(rootData2) + string(child2Data)
	if string(f2) != expected2 {
		t.Fatalf("child2 mismatch:\ngot:    %q\nexpect: %q", f2, expected2)
	}
}

func TestLinearizeRecordedForks(t *testing.T) {
	python := requirePython(t)
	tmp := t.TempDir()

	script := filepath.Join(tmp, "forktest.py")
	os.WriteFile(script, []byte(`import os, sys, time

pids = []
for i in range(2):
    pid = os.fork()
    if pid == 0:
        time.sleep(0.01)
        sys.exit(0)
    pids.append(pid)

for p in pids:
    os.waitpid(p, 0)
print("done")
`), 0644)

	trace := filepath.Join(tmp, "trace.retrace")
	recordTrace(t, python, script, trace)

	idx, err := IndexTrace(trace)
	if err != nil {
		t.Fatal(err)
	}

	outDir := filepath.Join(tmp, "linear")
	os.MkdirAll(outDir, 0755)

	files, err := Linearize(idx, outDir)
	if err != nil {
		t.Fatal(err)
	}

	t.Logf("generated %d linear files", len(files))
	if len(files) != 2 {
		t.Fatalf("expected 2 leaf files (2 children), got %d", len(files))
	}

	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}

		// Each linear file must start with a valid JSON preamble.
		nlIdx := strings.IndexByte(string(raw), '\n')
		if nlIdx < 0 {
			t.Fatalf("%s: no preamble newline found", f)
		}
		var preamble map[string]any
		if err := json.Unmarshal(raw[:nlIdx], &preamble); err != nil {
			t.Fatalf("%s: invalid preamble JSON: %v", f, err)
		}
		if preamble["type"] != "exec" {
			t.Errorf("%s: expected root preamble (type=exec), got type=%v", f, preamble["type"])
		}
		t.Logf("%s: %d bytes, preamble has executable=%v",
			filepath.Base(f), len(raw), preamble["executable"])
	}
}
