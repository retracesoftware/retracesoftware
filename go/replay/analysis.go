package replay

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"time"
)

// TraceAnalysis holds pre-computed analysis results for a recording.
type TraceAnalysis struct {
	LastCheckpointCursor *RawCursor `json:"last_checkpoint_cursor,omitempty"`
	SingleThreaded       bool       `json:"single_threaded"`
}

var runAnalysisProbe = runAnalysisProbeCommand

// EnsureTraceAnalysis lazily computes analysis for a PidFile, caching
// the result to disk under .retrace_cache/<trace>/analysis.json.
func EnsureTraceAnalysis(pidFile string) (*TraceAnalysis, error) {
	absPath, err := filepath.Abs(pidFile)
	if err != nil {
		return nil, err
	}

	cachePath := analysisPath(absPath)

	if isCacheFresh(absPath, cachePath) {
		if a, err := loadAnalysis(cachePath); err == nil {
			log.Printf("analysis cache hit: %s", cachePath)
			return a, nil
		}
	}

	log.Printf("running analysis probe: %s", absPath)

	a, err := runAnalysisProbe(absPath)
	if err != nil {
		return nil, err
	}

	if err := writeAnalysis(a, cachePath); err != nil {
		log.Printf("warning: failed to cache analysis: %v", err)
	}

	return a, nil
}

func analysisPath(absTrace string) string {
	cacheDir := filepath.Join(filepath.Dir(absTrace), ".retrace_cache", filepath.Base(absTrace))
	return filepath.Join(cacheDir, "analysis.json")
}

func loadAnalysis(path string) (*TraceAnalysis, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var a TraceAnalysis
	if err := json.Unmarshal(data, &a); err != nil {
		return nil, err
	}
	return &a, nil
}

func writeAnalysis(a *TraceAnalysis, path string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(a, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0644)
}

func runAnalysisProbeCommand(pidFile string) (*TraceAnalysis, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	rp, err := StartReplayFromPidFile(ctx, pidFile, nil, nil)
	if err != nil {
		return nil, fmt.Errorf("start replay: %w", err)
	}
	defer rp.Close()

	hit, err := rp.FindFirstBreakpoint(ctx, map[string]any{
		"function": "_thread.start_new_thread",
	})
	if err != nil {
		return nil, err
	}

	if hit == nil {
		return &TraceAnalysis{SingleThreaded: true}, nil
	}
	rc := hit.RawCursor()
	return &TraceAnalysis{
		LastCheckpointCursor: &rc,
		SingleThreaded:       false,
	}, nil
}
