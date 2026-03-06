package replay

import (
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestParseChunkOffsetsMonotonicWithDuplicateFinal(t *testing.T) {
	raw := []byte(`{"offset":0}
{"offset":10}
{"offset":10}
{"offset":25}
`)

	got, err := parseChunkOffsets(raw)
	if err != nil {
		t.Fatalf("parseChunkOffsets: %v", err)
	}
	want := []uint64{0, 10, 25}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("offsets mismatch: got %v want %v", got, want)
	}
}

func TestParseChunkOffsetsRejectsMalformedJSON(t *testing.T) {
	_, err := parseChunkOffsets([]byte("{bad-json}\n"))
	if err == nil {
		t.Fatal("expected error for malformed JSON")
	}
	if !strings.Contains(err.Error(), "invalid JSON") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestParseChunkOffsetsRejectsDecreasingOffsets(t *testing.T) {
	raw := []byte(`{"offset":5}
{"offset":3}
`)
	_, err := parseChunkOffsets(raw)
	if err == nil {
		t.Fatal("expected error for decreasing offsets")
	}
	if !strings.Contains(err.Error(), "previous") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestEnsureChunkOffsetsCachesProbeResult(t *testing.T) {
	oldProbe := runChunkProbe
	defer func() { runChunkProbe = oldProbe }()

	chunkMu.Lock()
	chunkByPath = map[string][]uint64{}
	chunkMu.Unlock()

	calls := 0
	runChunkProbe = func(path string, preamble map[string]any, chunkMS float64) ([]uint64, error) {
		calls++
		return []uint64{1, 2, 3}, nil
	}

	path := filepath.Join(t.TempDir(), "leaf.bin")
	preamble := map[string]any{"executable": "/usr/bin/python3", "cwd": "/tmp"}

	first, err := EnsureChunkOffsets(path, preamble, 100)
	if err != nil {
		t.Fatalf("first EnsureChunkOffsets: %v", err)
	}
	second, err := EnsureChunkOffsets(path, preamble, 100)
	if err != nil {
		t.Fatalf("second EnsureChunkOffsets: %v", err)
	}
	if calls != 1 {
		t.Fatalf("expected 1 probe call, got %d", calls)
	}
	want := []uint64{1, 2, 3}
	if !reflect.DeepEqual(first, want) || !reflect.DeepEqual(second, want) {
		t.Fatalf("unexpected offsets: first=%v second=%v", first, second)
	}

	// Returned slices should be copies, not shared cache backing.
	first[0] = 999
	third, err := EnsureChunkOffsets(path, preamble, 100)
	if err != nil {
		t.Fatalf("third EnsureChunkOffsets: %v", err)
	}
	if third[0] != 1 {
		t.Fatalf("expected cached value to be immutable copy, got %v", third)
	}
}
