// replay is the single entry point for working with Retrace recordings
// and PidFiles. It handles indexing, extraction, workspace generation,
// DAP debugging, and direct PidFile replay.
//
// Shebang usage: recordings contain #!/path/to/replay --recording
// so the OS invokes this binary with the recording path automatically.
//
// Usage:
//
//	replay --recording <path> --index           output process tree JSON
//	replay --recording <path> --extract [dir]   extract PidFiles
//	replay --recording <path> --workspace       generate .code-workspace
//	replay --recording <path> --dap [--pid N]   DAP proxy for a recording
//	replay <pidfile>                            replay a PidFile directly
//	replay --dap <pidfile>                      DAP proxy for a PidFile
//	replay --roundtrip <script>                 record then replay
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"

	"github.com/retracesoftware/replay/replay"
)

func main() {
	recording := flag.String("recording", "", "path to a .retrace recording (multi-process trace)")
	pid := flag.Int("pid", 0, "PID to replay from a recording (default: root process)")
	dap := flag.Bool("dap", false, "run as DAP proxy on stdin/stdout")
	index := flag.Bool("index", false, "output process tree index as JSON (requires --recording)")
	extract := flag.Bool("extract", false, "extract PidFiles from a recording (requires --recording)")
	workspace := flag.Bool("workspace", false, "generate a .code-workspace file (requires --recording)")
	debug := flag.Bool("debug", false, "enable verbose logging to stderr")
	verbose := flag.Bool("verbose", false, "enable verbose Python replay output")
	roundtrip := flag.String("roundtrip", "", "record a script via FIFO then replay it")

	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, `replay - retrace recording and PidFile tool

Usage:
  replay --recording <path> --index           output process tree JSON
  replay --recording <path> --extract [dir]   extract PidFiles to directory
  replay --recording <path> --workspace       generate .code-workspace file
  replay --recording <path> --dap [--pid N]   DAP proxy for a recording
  replay <pidfile>                            replay a PidFile directly
  replay --dap <pidfile>                      DAP proxy for a PidFile
  replay --roundtrip <script>                 record then replay

Recordings with a shebang are executable:
  ./recording.retrace --dap --pid 12345

Options:
`)
		flag.PrintDefaults()
	}

	flag.Parse()

	var dapWriter *replay.Writer
	if *dap {
		dapWriter = replay.NewWriter(os.Stdout)
		log.SetOutput(replay.NewDAPLogWriter(dapWriter))
		log.SetFlags(log.Ltime | log.Lmicroseconds)
	} else if *debug {
		log.SetFlags(log.Ltime | log.Lmicroseconds)
	} else {
		log.SetOutput(os.Stderr)
		log.SetFlags(0)
	}

	if *roundtrip != "" {
		if err := replay.Roundtrip(*roundtrip, os.Stdout, os.Stderr); err != nil {
			log.Fatalf("roundtrip: %v", err)
		}
		return
	}

	if *recording != "" {
		runRecordingMode(*recording, *pid, *dap, *index, *extract, *workspace, *verbose, dapWriter)
		return
	}

	if len(flag.Args()) < 1 {
		flag.Usage()
		os.Exit(1)
	}
	pidFile := flag.Args()[0]

	if *dap {
		proxy := replay.NewProxy(pidFile, os.Stdin, dapWriter)
		if err := proxy.Run(); err != nil {
			log.Fatalf("dap: %v", err)
		}
		return
	}

	var extraArgs []string
	if *verbose {
		extraArgs = append(extraArgs, "--verbose")
	}
	extraArgs = append(extraArgs, flag.Args()[1:]...)
	if err := replay.RunReplay(pidFile, os.Stdout, os.Stderr, 0, extraArgs...); err != nil {
		log.Fatalf("replay: %v", err)
	}
}

func runRecordingMode(recording string, pid int, dap, index, extract, workspace, verbose bool, dapWriter *replay.Writer) {
	if index {
		idx, err := replay.IndexTrace(recording)
		if err != nil {
			log.Fatalf("index: %v", err)
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(idx); err != nil {
			log.Fatalf("encode index: %v", err)
		}
		return
	}

	if workspace {
		wsPath, err := replay.GenerateWorkspace(recording)
		if err != nil {
			log.Fatalf("workspace: %v", err)
		}
		fmt.Println(wsPath)
		return
	}

	if extract {
		outDir := extractDir(recording)
		if len(flag.Args()) > 0 {
			outDir = flag.Args()[0]
		}
		if err := extractRecording(recording, outDir); err != nil {
			log.Fatalf("extract: %v", err)
		}
		return
	}

	if dap {
		pidFile, extractErr := ensureExtracted(recording, pid)
		proxy := replay.NewProxy(pidFile, os.Stdin, dapWriter)
		if extractErr != nil {
			proxy.SetLaunchError(extractErr)
		}
		if err := proxy.Run(); err != nil {
			log.Fatalf("dap: %v", err)
		}
		return
	}

	extraArgs := make([]string, 0, 1)
	if verbose {
		extraArgs = append(extraArgs, "--verbose")
	}

	if pid > 0 {
		pidFile, err := ensureExtracted(recording, pid)
		if err != nil {
			log.Fatalf("replay pid %d: %v", pid, err)
		}
		if err := replay.RunReplay(pidFile, os.Stdout, os.Stderr, 0, extraArgs...); err != nil {
			log.Fatalf("replay pid %d: %v", pid, err)
		}
		return
	}

	if err := replayAllExtracted(recording, extraArgs...); err != nil {
		log.Fatalf("replay all: %v", err)
	}
}

func extractDir(recording string) string {
	return strings.TrimSuffix(recording, filepath.Ext(recording)) + ".d"
}

func selfPath() string {
	self, err := os.Executable()
	if err != nil {
		return ""
	}
	self, _ = filepath.EvalSymlinks(self)
	return self
}

func extractRecording(recording, outDir string) error {
	idx, err := replay.IndexTrace(recording)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(outDir, 0755); err != nil {
		return err
	}
	indexPath := filepath.Join(outDir, "index.json")
	if err := replay.WriteIndex(idx, indexPath); err != nil {
		return err
	}
	log.Printf("wrote %s", indexPath)

	shebang := "#!/usr/bin/env replay"
	if bin := selfPath(); bin != "" {
		shebang = "#!" + bin
	}
	outputs, err := replay.LinearizeExecutable(idx, outDir, shebang)
	if err != nil {
		return err
	}
	for _, p := range outputs {
		log.Printf("wrote %s", p)
	}
	log.Printf("extracted %d PidFile(s) to %s", len(outputs), outDir)
	return nil
}

// ensureExtracted extracts the recording if needed and returns the
// PidFile path for the requested PID (or root if pid == 0).
func ensureExtracted(recording string, pid int) (string, error) {
	outDir := extractDir(recording)
	indexPath := filepath.Join(outDir, "index.json")

	if _, err := os.Stat(indexPath); os.IsNotExist(err) {
		if err := extractRecording(recording, outDir); err != nil {
			return "", err
		}
	}

	if pid == 0 {
		idx, err := replay.IndexTrace(recording)
		if err != nil {
			return "", err
		}
		pid = int(idx.Root.PID)
	}

	pidFile := filepath.Join(outDir, fmt.Sprintf("%d.bin", pid))
	if _, err := os.Stat(pidFile); err != nil {
		return "", fmt.Errorf("PidFile not found for pid %d: %s", pid, pidFile)
	}
	return pidFile, nil
}

func replayAllExtracted(recording string, extraArgs ...string) error {
	outDir := extractDir(recording)
	if _, err := ensureExtracted(recording, 0); err != nil {
		return err
	}

	idx, err := replay.IndexTrace(recording)
	if err != nil {
		return err
	}

	leafPIDs := make([]uint32, 0)
	collectLeafPIDs(idx.Root, &leafPIDs)
	if len(leafPIDs) == 0 {
		return fmt.Errorf("no leaf processes found for recording %s", recording)
	}

	for i, leafPID := range leafPIDs {
		pidFile := filepath.Join(outDir, fmt.Sprintf("%d.bin", leafPID))
		log.Printf("[%d/%d] replaying PID %d from %s", i+1, len(leafPIDs), leafPID, pidFile)
		if err := replay.RunReplay(pidFile, os.Stdout, os.Stderr, 0, extraArgs...); err != nil {
			return fmt.Errorf("pid %d: %w", leafPID, err)
		}
	}
	return nil
}

func collectLeafPIDs(p *replay.Process, out *[]uint32) {
	if p == nil {
		return
	}
	if len(p.Children) == 0 {
		*out = append(*out, p.PID)
		return
	}
	for _, c := range p.Children {
		collectLeafPIDs(c, out)
	}
}
