package replay

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
)

// findPython returns a Python interpreter that can import retracesoftware.
func findPython() (string, error) {
	for _, name := range []string{"python3", "python"} {
		p, err := exec.LookPath(name)
		if err != nil {
			continue
		}
		cmd := exec.Command(p, "-c", "import retracesoftware")
		if cmd.Run() == nil {
			return p, nil
		}
	}
	return "", fmt.Errorf("no Python with retracesoftware found on PATH")
}

// relay reads PID-framed data from r and writes the raw (unframed)
// payload for the first PID to w.  Frames for other PIDs are silently
// dropped.  The writer is closed when the relay finishes.
func relay(r io.Reader, w io.WriteCloser) error {
	defer w.Close()

	r = skipShebang(r)

	var mainPID uint32
	var header [6]byte

	for {
		if _, err := io.ReadFull(r, header[:]); err != nil {
			if err == io.EOF || err == io.ErrUnexpectedEOF {
				return nil
			}
			return fmt.Errorf("read frame header: %w", err)
		}

		pid := binary.LittleEndian.Uint32(header[:4])
		payloadLen := binary.LittleEndian.Uint16(header[4:6])

		if mainPID == 0 {
			mainPID = pid
		}

		if pid == mainPID {
			if _, err := io.CopyN(w, r, int64(payloadLen)); err != nil {
				if errors.Is(err, syscall.EPIPE) {
					return nil // reader closed (e.g. --list_pids)
				}
				return fmt.Errorf("relay payload: %w", err)
			}
		} else {
			if _, err := io.CopyN(io.Discard, r, int64(payloadLen)); err != nil {
				return fmt.Errorf("discard payload: %w", err)
			}
		}
	}
}

// Roundtrip records a Python script and simultaneously replays it.
//
// The flow:
//  1. Create a FIFO for record output
//  2. Start a Go pipe (relay -> replay stdin)
//  3. Start the replay process reading raw data from /dev/stdin
//  4. Start a relay goroutine: reads PID frames from the FIFO,
//     strips framing, writes raw payloads for the first PID into
//     the replay's stdin pipe
//  5. Start the record process writing to the FIFO
//  6. Data flows: record -> FIFO -> relay -> pipe -> replay stdin
//
// No seekable trace file is needed.
func Roundtrip(script string, stdout, stderr io.Writer, extraRecordArgs ...string) error {
	pythonBin, err := findPython()
	if err != nil {
		return err
	}

	tmpDir, err := os.MkdirTemp("", "retrace-roundtrip-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(tmpDir)

	recordFIFO := filepath.Join(tmpDir, "record.pipe")
	if err := syscall.Mkfifo(recordFIFO, 0600); err != nil {
		return fmt.Errorf("mkfifo: %w", err)
	}

	// Pipe: relay writes -> replay reads via stdin.
	pr, pw, err := os.Pipe()
	if err != nil {
		return fmt.Errorf("os.Pipe: %w", err)
	}

	// Start the replay process. It reads the unframed (non-PID-framed)
	// stream from /dev/stdin.
	replayArgs := []string{"-m", "retracesoftware",
		"--recording", "/dev/stdin", "--format", "unframed_binary", "--list_pids"}
	log.Printf("roundtrip replay: %s %v", pythonBin, replayArgs)

	replayCmd := exec.Command(pythonBin, replayArgs...)
	replayCmd.Stdin = pr
	replayCmd.Stdout = stdout
	replayCmd.Stderr = stderr

	if err := replayCmd.Start(); err != nil {
		pr.Close()
		pw.Close()
		return fmt.Errorf("start replay: %w", err)
	}
	pr.Close() // relay side keeps pw; replay has pr via inherited fd

	// Open the record FIFO for reading.  This blocks until the
	// record process opens the write end.
	fifoReady := make(chan struct{})
	var fifoFile *os.File
	var fifoErr error
	go func() {
		fifoFile, fifoErr = os.Open(recordFIFO)
		close(fifoReady)
	}()

	// Start the record process (opens recordFIFO for writing,
	// which unblocks the goroutine above).
	recordArgs := []string{"-m", "retracesoftware",
		"--recording", recordFIFO, "--"}
	recordArgs = append(recordArgs, extraRecordArgs...)
	recordArgs = append(recordArgs, script)
	log.Printf("roundtrip record: %s %v", pythonBin, recordArgs)

	recordCmd := exec.Command(pythonBin, recordArgs...)
	recordCmd.Stderr = stderr

	if err := recordCmd.Start(); err != nil {
		pw.Close()
		return fmt.Errorf("start record: %w", err)
	}

	<-fifoReady
	if fifoErr != nil {
		pw.Close()
		return fmt.Errorf("open record fifo: %w", fifoErr)
	}

	// Run the relay: reads PID frames from FIFO, writes raw
	// payloads for the first PID into pw (replay's stdin).
	relayErr := make(chan error, 1)
	go func() {
		relayErr <- relay(fifoFile, pw)
		fifoFile.Close()
	}()

	// Wait for the record process to finish (EOF on FIFO).
	recordErr := recordCmd.Wait()
	// Wait for the relay to drain remaining data.
	rErr := <-relayErr
	// Wait for the replay to finish.
	replayErr := replayCmd.Wait()

	if recordErr != nil {
		return fmt.Errorf("record: %w", recordErr)
	}
	if rErr != nil {
		return fmt.Errorf("relay: %w", rErr)
	}
	if replayErr != nil {
		return fmt.Errorf("replay: %w", replayErr)
	}
	return nil
}
