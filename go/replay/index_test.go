package replay

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func jsonPreamble(info map[string]any) []byte {
	j, _ := json.Marshal(info)
	return append(j, '\n')
}

func TestIndexTraceSingleProcess(t *testing.T) {
	preamble := jsonPreamble(map[string]any{
		"type":       "exec",
		"executable": "/usr/bin/python3",
		"cwd":        "/tmp",
	})
	data := []byte("some trace data after preamble")

	var trace []byte
	trace = append(trace, makePIDFrame(100, preamble)...)
	trace = append(trace, makePIDFrame(100, data)...)

	tmp := t.TempDir()
	path := filepath.Join(tmp, "trace.bin")
	os.WriteFile(path, trace, 0644)

	idx, err := IndexTrace(path)
	if err != nil {
		t.Fatal(err)
	}

	if idx.Root == nil {
		t.Fatal("root is nil")
	}
	if idx.Root.PID != 100 {
		t.Fatalf("expected root PID 100, got %d", idx.Root.PID)
	}
	if idx.Root.Type != "exec" {
		t.Fatalf("expected type exec, got %q", idx.Root.Type)
	}
	if len(idx.Root.Children) != 0 {
		t.Fatalf("expected no children, got %d", len(idx.Root.Children))
	}
	if len(idx.Root.Segments) == 0 {
		t.Fatal("expected at least one segment")
	}
}

func TestIndexTraceWithForks(t *testing.T) {
	rootPreamble := jsonPreamble(map[string]any{
		"type":       "exec",
		"executable": "/usr/bin/python3",
	})
	child1Preamble := jsonPreamble(map[string]any{
		"type":          "fork",
		"parent_pid":    100,
		"fork_index":    0,
		"parent_offset": 42,
	})
	child2Preamble := jsonPreamble(map[string]any{
		"type":          "fork",
		"parent_pid":    100,
		"fork_index":    1,
		"parent_offset": 99,
	})
	grandchildPreamble := jsonPreamble(map[string]any{
		"type":          "fork",
		"parent_pid":    200,
		"fork_index":    0,
		"parent_offset": 10,
	})

	data := []byte("payload")

	// Build interleaved trace: root, child1, root, child2, grandchild, root
	var trace []byte
	trace = append(trace, makePIDFrame(100, rootPreamble)...)
	trace = append(trace, makePIDFrame(100, data)...)
	trace = append(trace, makePIDFrame(200, child1Preamble)...)
	trace = append(trace, makePIDFrame(100, data)...)
	trace = append(trace, makePIDFrame(200, data)...)
	trace = append(trace, makePIDFrame(201, child2Preamble)...)
	trace = append(trace, makePIDFrame(300, grandchildPreamble)...)
	trace = append(trace, makePIDFrame(201, data)...)
	trace = append(trace, makePIDFrame(300, data)...)
	trace = append(trace, makePIDFrame(100, data)...)

	tmp := t.TempDir()
	path := filepath.Join(tmp, "trace.bin")
	os.WriteFile(path, trace, 0644)

	idx, err := IndexTrace(path)
	if err != nil {
		t.Fatal(err)
	}

	root := idx.Root
	if root.PID != 100 {
		t.Fatalf("root PID: expected 100, got %d", root.PID)
	}
	if root.Type != "exec" {
		t.Fatalf("root type: expected exec, got %q", root.Type)
	}

	if len(root.Children) != 2 {
		t.Fatalf("expected 2 children, got %d", len(root.Children))
	}

	c1 := root.Children[0]
	if c1.PID != 200 {
		t.Fatalf("child1 PID: expected 200, got %d", c1.PID)
	}
	if c1.ForkIndex != 0 {
		t.Fatalf("child1 fork_index: expected 0, got %d", c1.ForkIndex)
	}
	if c1.ParentPID != 100 {
		t.Fatalf("child1 parent_pid: expected 100, got %d", c1.ParentPID)
	}
	if c1.ParentOffset != 42 {
		t.Fatalf("child1 parent_offset: expected 42, got %d", c1.ParentOffset)
	}

	c2 := root.Children[1]
	if c2.PID != 201 {
		t.Fatalf("child2 PID: expected 201, got %d", c2.PID)
	}
	if c2.ForkIndex != 1 {
		t.Fatalf("child2 fork_index: expected 1, got %d", c2.ForkIndex)
	}
	if c2.ParentOffset != 99 {
		t.Fatalf("child2 parent_offset: expected 99, got %d", c2.ParentOffset)
	}

	if len(c1.Children) != 1 {
		t.Fatalf("child1 expected 1 grandchild, got %d", len(c1.Children))
	}
	gc := c1.Children[0]
	if gc.PID != 300 {
		t.Fatalf("grandchild PID: expected 300, got %d", gc.PID)
	}
	if gc.ParentPID != 200 {
		t.Fatalf("grandchild parent_pid: expected 200, got %d", gc.ParentPID)
	}
	if gc.ParentOffset != 10 {
		t.Fatalf("grandchild parent_offset: expected 10, got %d", gc.ParentOffset)
	}
}

func TestIndexTraceSegments(t *testing.T) {
	preamble := jsonPreamble(map[string]any{"type": "exec"})
	data := []byte("x")

	// Two contiguous frames for PID 100, then a PID 200 frame, then PID 100 again.
	// PID 100 should get 2 segments (the contiguous pair and the lone frame).
	var trace []byte
	trace = append(trace, makePIDFrame(100, preamble)...)
	trace = append(trace, makePIDFrame(100, data)...)
	trace = append(trace, makePIDFrame(200, jsonPreamble(map[string]any{
		"type":       "fork",
		"parent_pid": 100,
		"fork_index": 0,
	}))...)
	trace = append(trace, makePIDFrame(100, data)...)

	tmp := t.TempDir()
	path := filepath.Join(tmp, "trace.bin")
	os.WriteFile(path, trace, 0644)

	idx, err := IndexTrace(path)
	if err != nil {
		t.Fatal(err)
	}

	if len(idx.Root.Segments) != 2 {
		t.Fatalf("expected 2 segments for root, got %d", len(idx.Root.Segments))
	}

	// First segment covers the preamble + data frames (contiguous).
	s0 := idx.Root.Segments[0]
	if s0.Offset != 0 {
		t.Fatalf("segment 0 offset: expected 0, got %d", s0.Offset)
	}
	// Two contiguous frames: (6 + len(preamble)) + (6 + 1)
	expectedSize := int64(6+len(preamble)) + int64(6+1)
	if s0.Size != expectedSize {
		t.Fatalf("segment 0 size: expected %d, got %d", expectedSize, s0.Size)
	}
}

func TestIndexTraceWriteJSON(t *testing.T) {
	preamble := jsonPreamble(map[string]any{
		"type":       "exec",
		"executable": "/usr/bin/python3",
	})

	trace := makePIDFrame(100, preamble)
	tmp := t.TempDir()
	tracePath := filepath.Join(tmp, "trace.bin")
	os.WriteFile(tracePath, trace, 0644)

	idx, err := IndexTrace(tracePath)
	if err != nil {
		t.Fatal(err)
	}

	outPath := filepath.Join(tmp, "index.json")
	if err := WriteIndex(idx, outPath); err != nil {
		t.Fatal(err)
	}

	raw, err := os.ReadFile(outPath)
	if err != nil {
		t.Fatal(err)
	}

	var parsed TraceIndex
	if err := json.Unmarshal(raw, &parsed); err != nil {
		t.Fatalf("unmarshal index.json: %v", err)
	}
	if parsed.Root.PID != 100 {
		t.Fatalf("expected PID 100, got %d", parsed.Root.PID)
	}
	t.Logf("index.json:\n%s", string(raw))
}

// Integration test: record a forking Python script and index the trace.
func TestIndexTraceRecordedForks(t *testing.T) {
	python := requirePython(t)
	tmp := t.TempDir()

	script := filepath.Join(tmp, "forktest.py")
	os.WriteFile(script, []byte(`import os, sys, time

pids = []
for i in range(3):
    pid = os.fork()
    if pid == 0:
        # child: do a little work then exit
        time.sleep(0.01)
        sys.exit(0)
    pids.append(pid)

# parent: wait for all children
for p in pids:
    os.waitpid(p, 0)
print("all children done")
`), 0644)

	trace := filepath.Join(tmp, "trace.bin")
	recordTrace(t, python, script, trace)

	idx, err := IndexTrace(trace)
	if err != nil {
		t.Fatal(err)
	}

	if idx.Root == nil {
		t.Fatal("root is nil")
	}
	if idx.Root.Type != "exec" {
		t.Fatalf("root type: expected exec, got %q", idx.Root.Type)
	}

	t.Logf("root PID: %d, children: %d", idx.Root.PID, len(idx.Root.Children))
	for i, c := range idx.Root.Children {
		t.Logf("  child %d: PID=%d type=%s fork_index=%d parent_offset=%d",
			i, c.PID, c.Type, c.ForkIndex, c.ParentOffset)
	}

	if len(idx.Root.Children) != 3 {
		t.Fatalf("expected 3 children (3 forks), got %d", len(idx.Root.Children))
	}

	for i, c := range idx.Root.Children {
		if c.Type != "fork" {
			t.Errorf("child %d: expected type fork, got %q", i, c.Type)
		}
		if c.ParentPID != idx.Root.PID {
			t.Errorf("child %d: parent_pid %d != root PID %d", i, c.ParentPID, idx.Root.PID)
		}
		if c.ForkIndex != i {
			t.Errorf("child %d: expected fork_index %d, got %d", i, c.ForkIndex, i)
		}
		if c.ParentOffset <= 0 {
			t.Errorf("child %d: expected positive parent_offset, got %d", i, c.ParentOffset)
		}
	}

	// Verify JSON roundtrip
	outPath := filepath.Join(tmp, "index.json")
	if err := WriteIndex(idx, outPath); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(outPath)
	t.Logf("index.json:\n%s", string(raw))
}
