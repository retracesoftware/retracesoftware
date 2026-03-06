package replay

import (
	"context"
	"log"
	"sync"
)

// Debugger provides stateful debugging operations over a recorded trace.
// It tracks breakpoints and manages a merged hit list. Navigation and
// query operations live on the Cursor itself, not here.
type Debugger interface {
	AddBreakpoint(ctx context.Context, spec BreakpointSpec) (id int, err error)
	RemoveBreakpoint(id int)
	Hits() *HitList
	WaitForScans(ctx context.Context) error
	Close() error
}

// SimpleDebugger implements Debugger using a QueryEngine for breakpoint
// scanning. It owns only the stateful bookkeeping: breakpoint IDs, the
// merged HitList, and background scan goroutines.
type SimpleDebugger struct {
	engine QueryEngine
	hits   *HitList

	mu      sync.Mutex
	nextID  int
	cancels map[int]context.CancelFunc
	wg      sync.WaitGroup
}

func NewDebugger(engine QueryEngine) *SimpleDebugger {
	return &SimpleDebugger{
		engine:  engine,
		hits:    NewHitList(),
		cancels: make(map[int]context.CancelFunc),
	}
}

func (d *SimpleDebugger) AddBreakpoint(ctx context.Context, spec BreakpointSpec) (int, error) {
	d.mu.Lock()
	d.nextID++
	id := d.nextID
	bpCtx, cancel := context.WithCancel(ctx)
	d.cancels[id] = cancel
	d.mu.Unlock()

	d.wg.Add(1)
	go func() {
		defer d.wg.Done()
		d.runBreakpointScan(bpCtx, id, spec)
	}()
	return id, nil
}

func (d *SimpleDebugger) runBreakpointScan(ctx context.Context, id int, spec BreakpointSpec) {
	log.Printf("breakpoint scan[%d]: starting for %s:%d", id, spec.File, spec.Line)
	locations, errs := d.engine.BreakpointHits(ctx, spec.ToMap())
	count := 0
	for loc := range locations {
		count++
		log.Printf("breakpoint scan[%d]: hit at message_index=%d", id, loc.MessageIndex)
		d.hits.Insert(BreakpointHit{BreakpointID: id, Location: loc})
	}
	if err := <-errs; err != nil {
		log.Printf("breakpoint scan[%d]: error: %v", id, err)
	}
	log.Printf("breakpoint scan[%d]: complete, %d hits found", id, count)
}

func (d *SimpleDebugger) RemoveBreakpoint(id int) {
	d.mu.Lock()
	cancel, ok := d.cancels[id]
	if ok {
		delete(d.cancels, id)
	}
	d.mu.Unlock()

	if ok {
		cancel()
	}
	d.hits.RemoveByBreakpoint(id)
}

func (d *SimpleDebugger) Hits() *HitList {
	return d.hits
}

// WaitForScans blocks until all running breakpoint scans have finished
// or ctx is cancelled. This must be called before querying the hit list
// to avoid racing with background scan goroutines.
func (d *SimpleDebugger) WaitForScans(ctx context.Context) error {
	done := make(chan struct{})
	go func() {
		d.wg.Wait()
		close(done)
	}()
	select {
	case <-done:
		log.Printf("debugger: all breakpoint scans complete, %d hits", d.hits.Len())
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (d *SimpleDebugger) Close() error {
	d.mu.Lock()
	for id, cancel := range d.cancels {
		cancel()
		delete(d.cancels, id)
	}
	d.mu.Unlock()
	return d.engine.Close()
}
