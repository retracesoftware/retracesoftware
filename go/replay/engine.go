package replay

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"log"
)

// QueryEngine is a stateless query interface over a recorded trace.
// Its single operation streams breakpoint hit locations.
type QueryEngine interface {
	BreakpointHits(ctx context.Context, spec map[string]any) (<-chan Location, <-chan error)
	Provider() SnapshotProvider
	Close() error
}

// SimpleQueryEngine implements QueryEngine by starting a fresh replay
// process for every breakpoint scan. It also owns a SimpleSnapshotProvider
// for positioning replays at arbitrary locations.
type SimpleQueryEngine struct {
	root     *Replay
	pidFile  string
	provider *SimpleSnapshotProvider
	stderr   io.Writer
}

func NewQueryEngine(root *Replay, pidFile string, stderr io.Writer) *SimpleQueryEngine {
	return &SimpleQueryEngine{
		root:     root,
		pidFile:  pidFile,
		provider: NewSimpleSnapshotProvider(root),
		stderr:   stderr,
	}
}

func (e *SimpleQueryEngine) Provider() SnapshotProvider {
	return e.provider
}

func (e *SimpleQueryEngine) BreakpointHits(ctx context.Context, spec map[string]any) (<-chan Location, <-chan error) {
	out := make(chan Location)
	errs := make(chan error, 1)

	go func() {
		defer close(out)
		defer close(errs)

		log.Printf("engine: starting fresh replay for breakpoint spec %v from pidFile %s", spec, e.pidFile)
		var stderrBuf bytes.Buffer
		stderrW := io.MultiWriter(e.stderr, &stderrBuf)
		rp, err := StartReplayFromPidFile(ctx, e.pidFile, io.Discard, stderrW)
		if err != nil {
			errs <- fmt.Errorf("start replay: %w", err)
			return
		}
		defer rp.Close()
		log.Printf("engine: fresh replay started, sending FindBreakpoints")

		rpHits, rpErrs := rp.FindBreakpoints(ctx, spec)
		for loc := range rpHits {
			select {
			case <-ctx.Done():
				errs <- ctx.Err()
				return
			case out <- loc:
			}
		}
		if err := <-rpErrs; err != nil {
			log.Printf("engine: FindBreakpoints error: %v", err)
			errs <- err
		}
		log.Printf("engine: FindBreakpoints complete")
		if stderrBuf.Len() > 0 {
			log.Printf("engine: scan process stderr:\n%s", stderrBuf.String())
		}
	}()

	return out, errs
}

func (e *SimpleQueryEngine) Close() error {
	return e.root.Close()
}
