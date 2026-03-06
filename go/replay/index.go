package replay

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
)

// Segment is a contiguous run of PID-framed data in the source trace.
// Go can seek to Offset, read Size bytes, and strip 6-byte frame headers
// to recover raw payload for that PID.
type Segment struct {
	Offset int64 `json:"offset"`
	Size   int64 `json:"size"`
}

// Process is a node in the process tree.
type Process struct {
	PID          uint32         `json:"pid"`
	Type         string         `json:"type"`
	ParentPID    uint32         `json:"parent_pid,omitempty"`
	ForkIndex    int            `json:"fork_index,omitempty"`
	ParentOffset int64          `json:"parent_offset,omitempty"`
	Preamble     map[string]any `json:"preamble"`
	Segments     []Segment      `json:"segments"`
	Children     []*Process     `json:"children"`
}

// TraceIndex is the top-level index structure written to JSON.
type TraceIndex struct {
	TraceFile string   `json:"trace_file"`
	Root      *Process `json:"root"`
}

type pidState struct {
	preambleBuf  bytes.Buffer
	preambleDone bool
	preamble     map[string]any
	segments     []Segment
	runStart     int64
	runEnd       int64
	hasRun       bool
}

// IndexTrace performs a single pass over a PID-framed trace file and
// builds a process tree with byte-offset segments for each PID.
func IndexTrace(tracePath string) (*TraceIndex, error) {
	f, err := os.Open(tracePath)
	if err != nil {
		return nil, fmt.Errorf("open trace: %w", err)
	}
	defer f.Close()

	var pos int64

	// Skip optional shebang line.
	var peek [2]byte
	if n, _ := io.ReadFull(f, peek[:]); n == 2 && peek[0] == '#' && peek[1] == '!' {
		pos = 2
		var b [1]byte
		for {
			if _, err := io.ReadFull(f, b[:]); err != nil {
				return nil, fmt.Errorf("read shebang: %w", err)
			}
			pos++
			if b[0] == '\n' {
				break
			}
		}
	} else {
		f.Seek(0, io.SeekStart)
	}

	pids := make(map[uint32]*pidState)
	var pidOrder []uint32

	header := make([]byte, frameHeaderSize)
	for {
		frameStart := pos
		if _, err := io.ReadFull(f, header); err != nil {
			if err == io.EOF || err == io.ErrUnexpectedEOF {
				break
			}
			return nil, fmt.Errorf("read frame header at offset %d: %w", pos, err)
		}
		pos += frameHeaderSize

		pid := binary.LittleEndian.Uint32(header[0:4])
		payloadLen := int64(binary.LittleEndian.Uint16(header[4:6]))

		payload := make([]byte, payloadLen)
		if _, err := io.ReadFull(f, payload); err != nil {
			return nil, fmt.Errorf("read payload at offset %d: %w", pos, err)
		}
		pos += payloadLen

		st, seen := pids[pid]
		if !seen {
			st = &pidState{}
			pids[pid] = st
			pidOrder = append(pidOrder, pid)
		}

		if !st.preambleDone {
			st.preambleBuf.Write(payload)
			if idx := bytes.IndexByte(st.preambleBuf.Bytes(), '\n'); idx >= 0 {
				var m map[string]any
				if err := json.Unmarshal(st.preambleBuf.Bytes()[:idx], &m); err != nil {
					return nil, fmt.Errorf("parse preamble for pid %d: %w", pid, err)
				}
				st.preamble = m
				st.preambleDone = true
			}
		}

		if st.hasRun && st.runEnd == frameStart {
			st.runEnd = pos
		} else {
			if st.hasRun {
				st.segments = append(st.segments, Segment{
					Offset: st.runStart,
					Size:   st.runEnd - st.runStart,
				})
			}
			st.runStart = frameStart
			st.runEnd = pos
			st.hasRun = true
		}
	}

	for _, st := range pids {
		if st.hasRun {
			st.segments = append(st.segments, Segment{
				Offset: st.runStart,
				Size:   st.runEnd - st.runStart,
			})
		}
	}

	nodes := make(map[uint32]*Process)
	for _, pid := range pidOrder {
		st := pids[pid]
		p := &Process{
			PID:      pid,
			Preamble: st.preamble,
			Segments: st.segments,
			Children: []*Process{},
		}
		if st.preamble != nil {
			if t, ok := st.preamble["type"].(string); ok {
				p.Type = t
			}
			if pp, ok := st.preamble["parent_pid"].(float64); ok {
				p.ParentPID = uint32(pp)
			}
			if fi, ok := st.preamble["fork_index"].(float64); ok {
				p.ForkIndex = int(fi)
			}
			if po, ok := st.preamble["parent_offset"].(float64); ok {
				p.ParentOffset = int64(po)
			}
		}
		nodes[pid] = p
	}

	var root *Process
	for _, pid := range pidOrder {
		p := nodes[pid]
		if p.Type == "fork" && p.ParentPID != 0 {
			if parent, ok := nodes[p.ParentPID]; ok {
				parent.Children = append(parent.Children, p)
			}
		} else if root == nil {
			root = p
		}
	}

	if root == nil {
		return nil, fmt.Errorf("no root process found in trace")
	}

	var sortChildren func(*Process)
	sortChildren = func(p *Process) {
		sort.Slice(p.Children, func(i, j int) bool {
			return p.Children[i].ForkIndex < p.Children[j].ForkIndex
		})
		for _, c := range p.Children {
			sortChildren(c)
		}
	}
	sortChildren(root)

	return &TraceIndex{
		TraceFile: tracePath,
		Root:      root,
	}, nil
}

// WriteIndex serializes a TraceIndex to a JSON file.
func WriteIndex(idx *TraceIndex, outPath string) error {
	data, err := json.MarshalIndent(idx, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal index: %w", err)
	}
	return os.WriteFile(outPath, data, 0644)
}

// CWDs collects unique working directories from all process preambles.
func CWDs(idx *TraceIndex) []string {
	seen := map[string]bool{}
	var result []string
	collectCWDs(idx.Root, seen, &result)
	return result
}

func collectCWDs(p *Process, seen map[string]bool, result *[]string) {
	if cwd, ok := p.Preamble["cwd"].(string); ok && !seen[cwd] {
		seen[cwd] = true
		*result = append(*result, cwd)
	}
	for _, c := range p.Children {
		collectCWDs(c, seen, result)
	}
}

// GenerateWorkspace writes a .code-workspace file alongside the trace.
// Returns the absolute path of the generated file.
func GenerateWorkspace(tracePath string) (string, error) {
	abs, err := filepath.Abs(tracePath)
	if err != nil {
		return "", fmt.Errorf("resolve trace path: %w", err)
	}

	idx, err := IndexTrace(abs)
	if err != nil {
		return "", err
	}

	cwds := CWDs(idx)
	if len(cwds) == 0 {
		cwds = []string{filepath.Dir(abs)}
	}

	type folder struct {
		Path string `json:"path"`
	}
	type launchConfig struct {
		Type      string `json:"type"`
		Request   string `json:"request"`
		Name      string `json:"name"`
		Recording string `json:"recording"`
	}
	type launch struct {
		Version        string         `json:"version"`
		Configurations []launchConfig `json:"configurations"`
	}
	workspace := struct {
		Folders  []folder          `json:"folders"`
		Settings map[string]string `json:"settings"`
		Launch   launch            `json:"launch"`
	}{
		Settings: map[string]string{"retrace.recording": abs},
		Launch: launch{
			Version: "0.2.0",
			Configurations: []launchConfig{{
				Type:      "retrace",
				Request:   "launch",
				Name:      "Retrace",
				Recording: abs,
			}},
		},
	}
	for _, cwd := range cwds {
		workspace.Folders = append(workspace.Folders, folder{Path: cwd})
	}

	data, err := json.MarshalIndent(workspace, "", "  ")
	if err != nil {
		return "", fmt.Errorf("marshal workspace: %w", err)
	}

	ext := filepath.Ext(abs)
	base := abs[:len(abs)-len(ext)]
	outPath := base + ".code-workspace"

	if err := os.WriteFile(outPath, data, 0644); err != nil {
		return "", fmt.Errorf("write workspace: %w", err)
	}
	return outPath, nil
}
