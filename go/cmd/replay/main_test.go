package main

import (
	"encoding/binary"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestEnsureExtractedRebuildsStaleExtraction(t *testing.T) {
	tmp := t.TempDir()
	recording := filepath.Join(tmp, "pytest.retrace")

	writeTestTrace(t, recording, 100, "first")
	firstPidFile, err := ensureExtracted(recording, 0)
	if err != nil {
		t.Fatalf("first ensureExtracted: %v", err)
	}
	if filepath.Base(firstPidFile) != "100.bin" {
		t.Fatalf("first pidfile: expected 100.bin, got %s", firstPidFile)
	}

	outDir := extractDir(recording)
	indexPath := filepath.Join(outDir, "index.json")
	staleExtra := filepath.Join(outDir, "999.bin")
	if err := os.WriteFile(staleExtra, []byte("stale"), 0644); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-time.Hour)
	if err := os.Chtimes(indexPath, old, old); err != nil {
		t.Fatal(err)
	}

	writeTestTrace(t, recording, 200, "second")
	secondPidFile, err := ensureExtracted(recording, 0)
	if err != nil {
		t.Fatalf("second ensureExtracted: %v", err)
	}
	if filepath.Base(secondPidFile) != "200.bin" {
		t.Fatalf("second pidfile: expected 200.bin, got %s", secondPidFile)
	}
	if _, err := os.Stat(filepath.Join(outDir, "100.bin")); !os.IsNotExist(err) {
		t.Fatalf("stale pidfile was not removed: %v", err)
	}
	if _, err := os.Stat(staleExtra); !os.IsNotExist(err) {
		t.Fatalf("stale extra file was not removed: %v", err)
	}
}

func writeTestTrace(t *testing.T, path string, pid uint32, payload string) {
	t.Helper()

	preamble := testJSONPreamble(t, map[string]any{
		"type":       "exec",
		"executable": "/usr/bin/python3",
	})
	var trace []byte
	trace = append(trace, testPIDFrame(pid, preamble)...)
	trace = append(trace, testPIDFrame(pid, []byte(payload))...)

	if err := os.WriteFile(path, trace, 0644); err != nil {
		t.Fatal(err)
	}
}

func testJSONPreamble(t *testing.T, info map[string]any) []byte {
	t.Helper()

	data, err := json.Marshal(info)
	if err != nil {
		t.Fatal(err)
	}
	return append(data, '\n')
}

func testPIDFrame(pid uint32, payload []byte) []byte {
	frame := make([]byte, 6+len(payload))
	binary.LittleEndian.PutUint32(frame[0:4], pid)
	binary.LittleEndian.PutUint16(frame[4:6], uint16(len(payload)))
	copy(frame[6:], payload)
	return frame
}
