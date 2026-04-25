package replay

import (
	"fmt"
	"io"
	"log"
	"os/exec"
)

var (
	buildCommand = pythonCommand
	runCommand   = func(cmd *exec.Cmd) error { return cmd.Run() }
	startCommand = func(cmd *exec.Cmd) error { return cmd.Start() }
	waitCommand  = func(cmd *exec.Cmd) error { return cmd.Wait() }
)

// RunReplay launches a non-interactive Python replay process for the
// given PidFile and blocks until it exits. The PidFile's embedded
// preamble supplies the python binary and cwd.
func RunReplay(pidFile string, stdout, stderr io.Writer, chunkMS float64, extraArgs ...string) error {
	process, err := ReadProcess(pidFile)
	if err != nil {
		return fmt.Errorf("read pidfile preamble: %w", err)
	}
	target, err := targetFromProcess(process)
	if err != nil {
		return err
	}

	if chunkMS > 0 {
		if _, err := EnsureChunkOffsets(target.Recording, target.Preamble, chunkMS); err != nil {
			return fmt.Errorf("chunk precompute: %w", err)
		}
	}

	cmdArgs := []string{"-m", "retracesoftware", "--recording", target.Recording}
	cmdArgs = append(cmdArgs, extraArgs...)
	log.Printf("replay run: %s %v (cwd=%s)", target.PythonBin, cmdArgs, target.CWD)

	cmd := buildCommand(target.PythonBin, cmdArgs...)
	cmd.Dir = target.CWD
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	cmd.Stdin = nil
	if err := runCommand(cmd); err != nil {
		return fmt.Errorf("replay exited: %w", err)
	}
	return nil
}
