package replay

import (
	"fmt"
	"io"
	"log"
	"path/filepath"
)

// ReplayAll builds the cache for a trace file and replays every leaf
// in the process tree sequentially.  Each leaf is a linearized file
// that Python can consume as a raw recording.
func ReplayAll(tracePath string, stdout, stderr io.Writer, extraArgs ...string) error {
	return ReplayAllWithChunk(tracePath, stdout, stderr, 0, extraArgs...)
}

// ReplayAllWithChunk optionally precomputes per-leaf chunk offsets before replay.
func ReplayAllWithChunk(tracePath string, stdout, stderr io.Writer, chunkMS float64, extraArgs ...string) error {
	idx, cacheDir, err := PrepareCache(tracePath)
	if err != nil {
		return err
	}

	var leaves []*Process
	collectLeaves(idx.Root, &leaves)

	log.Printf("replaying %d leaf process(es)", len(leaves))

	var firstErr error
	for i, leaf := range leaves {
		leafPath := filepath.Join(cacheDir, fmt.Sprintf("%d.bin", leaf.PID))
		log.Printf("[%d/%d] replaying PID %d from %s", i+1, len(leaves), leaf.PID, leafPath)

		if err := RunReplay(leafPath, stdout, stderr, chunkMS, extraArgs...); err != nil {
			log.Printf("PID %d failed: %v", leaf.PID, err)
			if firstErr == nil {
				firstErr = fmt.Errorf("pid %d: %w", leaf.PID, err)
			}
		}
	}
	return firstErr
}

// ReplayCached replays a single PID from the cache.
func ReplayCached(tracePath string, pid uint32, stdout, stderr io.Writer, extraArgs ...string) error {
	return ReplayCachedWithChunk(tracePath, pid, stdout, stderr, 0, extraArgs...)
}

// ReplayCachedWithChunk optionally precomputes chunk offsets for the selected PID.
func ReplayCachedWithChunk(tracePath string, pid uint32, stdout, stderr io.Writer, chunkMS float64, extraArgs ...string) error {
	_, cacheDir, err := PrepareCache(tracePath)
	if err != nil {
		return err
	}

	leafPath := filepath.Join(cacheDir, fmt.Sprintf("%d.bin", pid))
	return RunReplay(leafPath, stdout, stderr, chunkMS, extraArgs...)
}

func collectLeaves(p *Process, out *[]*Process) {
	if len(p.Children) == 0 {
		*out = append(*out, p)
		return
	}
	for _, c := range p.Children {
		collectLeaves(c, out)
	}
}
