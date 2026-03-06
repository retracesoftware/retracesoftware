package replay

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

// skipShebang reads past an optional "#!" shebang line.
// If the first two bytes are not "#!", they are returned unread
// via an io.MultiReader wrapper.
func skipShebang(r io.Reader) io.Reader {
	var peek [2]byte
	n, err := io.ReadFull(r, peek[:])
	if n < 2 || err != nil {
		return io.MultiReader(bytes.NewReader(peek[:n]), r)
	}
	if peek[0] == '#' && peek[1] == '!' {
		br := bufio.NewReader(r)
		br.ReadBytes('\n')
		return br
	}
	return io.MultiReader(bytes.NewReader(peek[:]), r)
}

const frameHeaderSize = 6

// FirstPID opens a trace file, skips an optional shebang, and returns
// the PID from the first frame header.
func FirstPID(tracePath string) (uint32, error) {
	f, err := os.Open(tracePath)
	if err != nil {
		return 0, fmt.Errorf("open trace: %w", err)
	}
	defer f.Close()

	reader := skipShebang(f)

	var header [frameHeaderSize]byte
	if _, err := io.ReadFull(reader, header[:]); err != nil {
		return 0, fmt.Errorf("read first frame header: %w", err)
	}
	return binary.LittleEndian.Uint32(header[0:4]), nil
}

// DemuxPID reads a PID-framed trace file and writes only the frames
// for the given PID (with framing stripped) to outPath.
func DemuxPID(tracePath string, pid uint32, outPath string) error {
	f, err := os.Open(tracePath)
	if err != nil {
		return fmt.Errorf("open trace: %w", err)
	}
	defer f.Close()

	reader := skipShebang(f)

	out, err := os.Create(outPath)
	if err != nil {
		return fmt.Errorf("create output: %w", err)
	}
	defer out.Close()

	header := make([]byte, frameHeaderSize)
	for {
		if _, err := io.ReadFull(reader, header); err != nil {
			if err == io.EOF || err == io.ErrUnexpectedEOF {
				return nil
			}
			return fmt.Errorf("read frame header: %w", err)
		}

		framePID := binary.LittleEndian.Uint32(header[0:4])
		payloadLen := binary.LittleEndian.Uint16(header[4:6])

		if framePID == pid {
			if _, err := io.CopyN(out, reader, int64(payloadLen)); err != nil {
				return fmt.Errorf("copy payload: %w", err)
			}
		} else {
			if _, err := io.CopyN(io.Discard, reader, int64(payloadLen)); err != nil {
				return fmt.Errorf("discard payload: %w", err)
			}
		}
	}
}

// Demux reads a PID-framed trace file and splits it into per-PID files
// with frame headers stripped. Each output file contains the concatenated
// raw payloads for one PID, named <pid>.bin.
//
// It runs asynchronously: the returned channel emits the file path the
// first time each PID is seen, allowing the caller to start reading
// per-PID files (e.g. preambles) before the entire trace has been
// processed. The channel is closed when processing finishes. Call the
// returned wait function to block until completion and retrieve any error.
func Demux(tracePath, outDir string) (<-chan string, func() error) {
	ch := make(chan string)
	errCh := make(chan error, 1)

	go func() {
		defer close(ch)
		errCh <- demuxLoop(tracePath, outDir, ch)
	}()

	wait := func() error { return <-errCh }
	return ch, wait
}

func demuxLoop(tracePath, outDir string, ch chan<- string) error {
	f, err := os.Open(tracePath)
	if err != nil {
		return fmt.Errorf("open trace: %w", err)
	}
	defer f.Close()

	reader := skipShebang(f)

	writers := make(map[uint32]*os.File)
	defer func() {
		for _, w := range writers {
			w.Close()
		}
	}()

	header := make([]byte, frameHeaderSize)
	for {
		if _, err := io.ReadFull(reader, header); err != nil {
			if err == io.EOF || err == io.ErrUnexpectedEOF {
				return nil
			}
			return fmt.Errorf("read frame header: %w", err)
		}

		pid := binary.LittleEndian.Uint32(header[0:4])
		payloadLen := binary.LittleEndian.Uint16(header[4:6])

		payload := make([]byte, payloadLen)
		if _, err := io.ReadFull(reader, payload); err != nil {
			return fmt.Errorf("read frame payload (pid %d, len %d): %w", pid, payloadLen, err)
		}

		w, seen := writers[pid]
		if !seen {
			p := filepath.Join(outDir, fmt.Sprintf("%d.bin", pid))
			w, err = os.Create(p)
			if err != nil {
				return fmt.Errorf("create output for pid %d: %w", pid, err)
			}
			writers[pid] = w
		}

		if _, err := w.Write(payload); err != nil {
			return fmt.Errorf("write payload for pid %d: %w", pid, err)
		}

		if !seen {
			ch <- filepath.Join(outDir, fmt.Sprintf("%d.bin", pid))
		}
	}
}
