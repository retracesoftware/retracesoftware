package replay

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
)

var (
	chunkMu       sync.RWMutex
	chunkByPath   = map[string][]uint64{}
	runChunkProbe = runChunkProbeCommand
)

// CachedChunkOffsets returns a copy of the in-memory offsets for a recording.
func CachedChunkOffsets(path string) ([]uint64, bool) {
	key := chunkKey(path)
	chunkMu.RLock()
	values, ok := chunkByPath[key]
	chunkMu.RUnlock()
	if !ok {
		return nil, false
	}
	out := make([]uint64, len(values))
	copy(out, values)
	return out, true
}

// EnsureChunkOffsets lazily computes message-index chunk offsets for a recording.
func EnsureChunkOffsets(path string, preamble map[string]any, chunkMS float64) ([]uint64, error) {
	if chunkMS <= 0 {
		return nil, nil
	}

	key := chunkKey(path)
	if offsets, ok := CachedChunkOffsets(key); ok {
		return offsets, nil
	}

	offsets, err := runChunkProbe(path, preamble, chunkMS)
	if err != nil {
		return nil, err
	}

	chunkMu.Lock()
	// Another goroutine may have populated while we were probing.
	if existing, ok := chunkByPath[key]; ok {
		chunkMu.Unlock()
		out := make([]uint64, len(existing))
		copy(out, existing)
		return out, nil
	}
	chunkByPath[key] = offsets
	chunkMu.Unlock()

	out := make([]uint64, len(offsets))
	copy(out, offsets)
	return out, nil
}

func chunkKey(path string) string {
	abs, err := filepath.Abs(path)
	if err != nil {
		return path
	}
	return abs
}

func runChunkProbeCommand(path string, preamble map[string]any, chunkMS float64) ([]uint64, error) {
	pythonBin, _ := preamble["executable"].(string)
	if pythonBin == "" {
		return nil, fmt.Errorf("chunk probe preamble missing 'executable'")
	}

	cmdArgs := []string{
		"-m", "retracesoftware",
		"--recording", path,
		"--chunk_ms", strconv.FormatFloat(chunkMS, 'f', -1, 64),
	}
	cmd := pythonCommand(pythonBin, cmdArgs...)
	if cwd, _ := preamble["cwd"].(string); cwd != "" {
		cmd.Dir = cwd
	}

	var stdoutBuf bytes.Buffer
	var stderrBuf bytes.Buffer
	cmd.Stdout = &stdoutBuf
	cmd.Stderr = &stderrBuf
	cmd.Stdin = nil

	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf(
			"chunk probe replay exited: %w (stderr: %s)",
			err,
			strings.TrimSpace(stderrBuf.String()),
		)
	}

	offsets, err := parseChunkOffsets(stdoutBuf.Bytes())
	if err != nil {
		return nil, fmt.Errorf("parse chunk offsets: %w", err)
	}
	return offsets, nil
}

func parseChunkOffsets(raw []byte) ([]uint64, error) {
	scanner := bufio.NewScanner(bytes.NewReader(raw))
	scanner.Buffer(make([]byte, 0, 64*1024), 16*1024*1024)

	offsets := make([]uint64, 0, 64)
	lineNo := 0
	for scanner.Scan() {
		lineNo++
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}

		var record struct {
			Offset *uint64 `json:"offset"`
		}
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			return nil, fmt.Errorf("line %d: invalid JSON: %w", lineNo, err)
		}
		if record.Offset == nil {
			return nil, fmt.Errorf("line %d: missing 'offset'", lineNo)
		}

		offset := *record.Offset
		if len(offsets) > 0 {
			prev := offsets[len(offsets)-1]
			if offset < prev {
				return nil, fmt.Errorf("line %d: offset %d < previous %d", lineNo, offset, prev)
			}
			if offset == prev {
				continue
			}
		}
		offsets = append(offsets, offset)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if len(offsets) == 0 {
		return nil, fmt.Errorf("no chunk offsets emitted")
	}
	return offsets, nil
}
