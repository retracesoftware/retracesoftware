package replay

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
)

// PrepareCache ensures the cache directory for the given trace file
// exists and contains the index and linearized leaf files.
//
// The cache lives at <trace_dir>/.retrace_cache/<trace_basename>/
// and is regenerated when the trace file is newer than the cached index.
//
// Returns the loaded index and the path to the cache directory.
func PrepareCache(tracePath string) (*TraceIndex, string, error) {
	absTrace, err := filepath.Abs(tracePath)
	if err != nil {
		return nil, "", err
	}

	cacheDir := filepath.Join(filepath.Dir(absTrace), ".retrace_cache", filepath.Base(absTrace))
	indexPath := filepath.Join(cacheDir, "index.json")

	if isCacheFresh(absTrace, indexPath) {
		idx, err := loadIndex(indexPath)
		if err == nil {
			log.Printf("cache hit: %s", cacheDir)
			return idx, cacheDir, nil
		}
		log.Printf("cache corrupt, rebuilding: %v", err)
	}

	log.Printf("building cache: %s", cacheDir)

	idx, err := IndexTrace(absTrace)
	if err != nil {
		return nil, "", fmt.Errorf("index: %w", err)
	}

	if err := os.MkdirAll(cacheDir, 0755); err != nil {
		return nil, "", err
	}

	if err := WriteIndex(idx, indexPath); err != nil {
		return nil, "", fmt.Errorf("write index: %w", err)
	}

	if _, err := Linearize(idx, cacheDir); err != nil {
		return nil, "", fmt.Errorf("linearize: %w", err)
	}

	return idx, cacheDir, nil
}

func isCacheFresh(tracePath, indexPath string) bool {
	traceStat, err := os.Stat(tracePath)
	if err != nil {
		return false
	}
	indexStat, err := os.Stat(indexPath)
	if err != nil {
		return false
	}
	return indexStat.ModTime().After(traceStat.ModTime())
}

func loadIndex(path string) (*TraceIndex, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var idx TraceIndex
	if err := json.Unmarshal(data, &idx); err != nil {
		return nil, err
	}
	if idx.Root == nil {
		return nil, fmt.Errorf("index has no root")
	}
	return &idx, nil
}
