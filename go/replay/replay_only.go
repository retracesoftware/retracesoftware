package replay

import (
	"fmt"
	"io"
	"os"
)

// ReplayOnly extracts the stream for a single PID from a trace,
// resolves the process info, and launches the Python replay.
func ReplayOnly(tracePath string, pid uint32, stdout, stderr io.Writer, extraArgs ...string) error {
	return ReplayOnlyWithChunk(tracePath, pid, stdout, stderr, 0, extraArgs...)
}

// ReplayOnlyWithChunk optionally precomputes chunk offsets before replay.
func ReplayOnlyWithChunk(tracePath string, pid uint32, stdout, stderr io.Writer, chunkMS float64, extraArgs ...string) error {
	process, tmpDir, err := ResolveProcess(tracePath, pid)
	if err != nil {
		return fmt.Errorf("resolve process: %w", err)
	}
	defer os.RemoveAll(tmpDir)

	pidFile, _ := process["recording"].(string)
	if pidFile == "" {
		return fmt.Errorf("resolved process missing recording path")
	}

	return RunReplay(pidFile, stdout, stderr, chunkMS, extraArgs...)
}
