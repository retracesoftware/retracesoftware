package replay

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
)

// ResolveProcess extracts the stream for a single PID from a
// PID-framed trace file, reads the process info from it, and returns
// it along with the temporary directory containing the filtered
// stream. The caller is responsible for cleaning up tmpDir when done.
func ResolveProcess(tracePath string, pid uint32) (process map[string]any, tmpDir string, err error) {
	tmpDir, err = os.MkdirTemp("", "retrace-dap-")
	if err != nil {
		return nil, "", err
	}

	outPath := filepath.Join(tmpDir, fmt.Sprintf("%d.bin", pid))
	if err := DemuxPID(tracePath, pid, outPath); err != nil {
		os.RemoveAll(tmpDir)
		return nil, "", fmt.Errorf("demux pid %d: %w", pid, err)
	}

	process, err = ReadProcess(outPath)
	if err != nil {
		os.RemoveAll(tmpDir)
		return nil, "", fmt.Errorf("read process for pid %d: %w", pid, err)
	}

	log.Printf("resolved process pid=%d: executable=%v recording=%v",
		pid, process["executable"], process["recording"])
	return process, tmpDir, nil
}
