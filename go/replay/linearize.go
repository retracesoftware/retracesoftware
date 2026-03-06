package replay

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

// Linearize generates one unframed replay file per leaf in the process
// tree.  Each file is named <leaf_pid>.bin and contains the stitched
// raw payload from root through to that leaf — parent data up to the
// fork point, then child data (preamble stripped), and so on.
//
// The root preamble is preserved at the start of every file so Python
// can read process info normally.
func Linearize(idx *TraceIndex, outDir string) ([]string, error) {
	return linearize(idx, outDir, "")
}

// LinearizeExecutable works like Linearize but prepends a shebang line
// to each output file and marks them executable (0755). The shebang
// should be a full interpreter path, e.g. "#!/usr/bin/env replay".
func LinearizeExecutable(idx *TraceIndex, outDir, shebang string) ([]string, error) {
	return linearize(idx, outDir, shebang)
}

func linearize(idx *TraceIndex, outDir, shebang string) ([]string, error) {
	tmpDir, err := os.MkdirTemp("", "retrace-linearize-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(tmpDir)

	ch, waitDemux := Demux(idx.TraceFile, tmpDir)
	for range ch {
	}
	if err := waitDemux(); err != nil {
		return nil, fmt.Errorf("demux: %w", err)
	}

	var paths [][]*Process
	collectPaths(idx.Root, nil, &paths)

	var outputs []string
	for _, path := range paths {
		leaf := path[len(path)-1]
		outPath := filepath.Join(outDir, fmt.Sprintf("%d.bin", leaf.PID))
		if err := stitchPath(path, tmpDir, outPath); err != nil {
			return nil, fmt.Errorf("stitch pid %d: %w", leaf.PID, err)
		}
		if shebang != "" {
			if err := prependShebang(outPath, shebang); err != nil {
				return nil, fmt.Errorf("shebang pid %d: %w", leaf.PID, err)
			}
			if err := os.Chmod(outPath, 0755); err != nil {
				return nil, fmt.Errorf("chmod pid %d: %w", leaf.PID, err)
			}
		}
		outputs = append(outputs, outPath)
	}
	return outputs, nil
}

// collectPaths finds all root-to-leaf paths in the tree.
func collectPaths(p *Process, current []*Process, out *[][]*Process) {
	current = append(current, p)
	if len(p.Children) == 0 {
		path := make([]*Process, len(current))
		copy(path, current)
		*out = append(*out, path)
		return
	}
	for _, c := range p.Children {
		collectPaths(c, current, out)
	}
}

// stitchPath writes a single linear file for a root-to-leaf path.
func stitchPath(path []*Process, demuxDir, outPath string) error {
	out, err := os.Create(outPath)
	if err != nil {
		return err
	}
	defer out.Close()

	bw := bufio.NewWriter(out)

	for i, p := range path {
		pidFile := filepath.Join(demuxDir, fmt.Sprintf("%d.bin", p.PID))
		f, err := os.Open(pidFile)
		if err != nil {
			return fmt.Errorf("open %s: %w", pidFile, err)
		}

		var start int64
		if i > 0 {
			// Skip the fork preamble (everything up to and including \n).
			size, err := preambleSize(f)
			if err != nil {
				f.Close()
				return fmt.Errorf("preamble size pid %d: %w", p.PID, err)
			}
			start = size
		}

		// How many bytes of this PID's file to copy.
		// For non-leaf nodes: up to the next child's fork cut point.
		// For the leaf: everything to end of file.
		var limit int64 = -1
		if i < len(path)-1 {
			next := path[i+1]
			cutPoint := next.ParentOffset - p.ParentOffset
			if i == 0 {
				cutPoint = next.ParentOffset
			}
			limit = cutPoint - start
		}

		if _, err := f.Seek(start, io.SeekStart); err != nil {
			f.Close()
			return err
		}

		if limit >= 0 {
			if _, err := io.CopyN(bw, f, limit); err != nil {
				f.Close()
				return fmt.Errorf("copy pid %d: %w", p.PID, err)
			}
		} else {
			if _, err := io.Copy(bw, f); err != nil {
				f.Close()
				return fmt.Errorf("copy pid %d: %w", p.PID, err)
			}
		}
		f.Close()
	}

	return bw.Flush()
}

// preambleSize reads a demuxed per-PID file and returns the number
// of bytes consumed by the JSON preamble line (including the \n).
func preambleSize(f *os.File) (int64, error) {
	r := bufio.NewReader(f)
	line, err := r.ReadBytes('\n')
	if err != nil {
		return 0, fmt.Errorf("read preamble: %w", err)
	}
	return int64(len(line)), nil
}

// prependShebang rewrites a file to have a shebang line at the start.
func prependShebang(path, shebang string) error {
	existing, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	header := shebang + "\n"
	if _, err := f.WriteString(header); err != nil {
		return err
	}
	_, err = f.Write(existing)
	return err
}
