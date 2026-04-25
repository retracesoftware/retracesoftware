package replay

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"iter"
	"log"
	"slices"
)

var ErrNotImplemented = errors.New("not implemented")

func isStopFailure(reason string) bool {
	return reason == "eof" || reason == "overshoot"
}

// FunctionCounts is a per-frame call-count path through the execution tree.
// Each element records how many child calls the function at that depth has
// dispatched so far. Dropping the last element gives the parent frame.
type FunctionCounts []int

// Depth returns the call-stack depth (number of frames).
func (fc FunctionCounts) Depth() int { return len(fc) }

// Parent returns the counts with the innermost frame removed.
// Returns nil for an empty (root) path.
func (fc FunctionCounts) Parent() FunctionCounts {
	if len(fc) == 0 {
		return nil
	}
	out := make(FunctionCounts, len(fc)-1)
	copy(out, fc[:len(fc)-1])
	return out
}

// PreviousCall finds the most recent child call by trimming trailing
// zeros, decrementing the last non-zero element, and repeating if the
// decrement produces another zero (since a 0 suffix is a function
// entry, not a call). Returns ok=false when no previous call exists.
func (fc FunctionCounts) PreviousCall() (result FunctionCounts, ok bool) {
	result = make(FunctionCounts, len(fc))
	copy(result, fc)
	for {
		for len(result) > 0 && result[len(result)-1] == 0 {
			result = result[:len(result)-1]
		}
		if len(result) == 0 {
			return nil, false
		}
		result[len(result)-1]--
		if result[len(result)-1] > 0 {
			return result, true
		}
	}
}

// Compare returns -1 if fc is earlier in execution than other, +1 if
// later, 0 if equal. Lexicographic on the shared prefix; when one is a
// prefix of the other the longer (deeper) array is earlier because it
// represents a position inside a child call that hasn't returned yet.
func (fc FunctionCounts) Compare(other FunctionCounts) int {
	n := len(fc)
	if len(other) < n {
		n = len(other)
	}
	for i := 0; i < n; i++ {
		if fc[i] < other[i] {
			return -1
		}
		if fc[i] > other[i] {
			return 1
		}
	}
	if len(fc) > len(other) {
		return -1
	}
	if len(fc) < len(other) {
		return 1
	}
	return 0
}

// Before reports whether fc is strictly earlier in execution than other.
func (fc FunctionCounts) Before(other FunctionCounts) bool {
	return fc.Compare(other) < 0
}

func Map[A, B any](seq iter.Seq[A], f func(A) B) iter.Seq[B] {
	return func(yield func(B) bool) {
		for a := range seq {
			if !yield(f(a)) {
				return
			}
		}
	}
}

// Decreasing yields FunctionCounts in strictly decreasing execution order,
// starting from fc itself. After exhausting decrements at the deepest
// frame it pops up to the parent and continues.
//
//	[1,2,4,1] → [1,2,4,0] → [1,2,4] → [1,2,3] → [1,2,2] → …
func (fc FunctionCounts) Decreasing() iter.Seq[FunctionCounts] {
	return func(yield func(FunctionCounts) bool) {
		cur := slices.Clone([]int(fc))
		for len(cur) > 0 {
			if !yield(FunctionCounts(slices.Clone(cur))) {
				return
			}
			cur[len(cur)-1]--
			for len(cur) > 0 && cur[len(cur)-1] < 0 {
				cur = cur[:len(cur)-1]
				if len(cur) > 0 {
					if !yield(FunctionCounts(slices.Clone(cur))) {
						return
					}
					cur[len(cur)-1]--
				}
			}
		}
	}
}

// Location is a pure-data position within a recorded trace. It holds the
// thread ID, per-frame call counts, optional bytecode offset, and message
// index. It is cheap to copy and safe to store in bulk (e.g. HitList).
type Location struct {
	ThreadID       uint64         `json:"thread_id"`
	FunctionCounts FunctionCounts `json:"function_counts"`
	FLasti         *int           `json:"f_lasti,omitempty"`
	Lineno         int            `json:"lineno,omitempty"`
	MessageIndex   uint64         `json:"message_index"`
}

func (l Location) Equal(other Location) bool {
	if l.ThreadID != other.ThreadID {
		return false
	}
	if l.FunctionCounts.Compare(other.FunctionCounts) != 0 {
		return false
	}
	if l.FLasti == nil && other.FLasti == nil {
		return true
	}
	if l.FLasti == nil || other.FLasti == nil {
		return false
	}
	return *l.FLasti == *other.FLasti
}

func (l Location) IsZero() bool {
	return l.ThreadID == 0 && len(l.FunctionCounts) == 0 && l.MessageIndex == 0
}

func (l Location) RawCursor() RawCursor {
	return RawCursor{
		ThreadID:       l.ThreadID,
		FunctionCounts: l.FunctionCounts,
		FLasti:         l.FLasti,
		Lineno:         l.Lineno,
	}
}

// Cursor is a lightweight position in a recorded trace with an optional
// cached *Replay. Query methods lazily materialise the replay from the
// SnapshotProvider. Navigation methods may steal the cache for the new
// Cursor, leaving the old Cursor valid but uncached.
type Cursor struct {
	location Location
	provider SnapshotProvider
	replay   *Replay // optional cache, may be nil
}

func NewCursor(loc Location, provider SnapshotProvider, replay *Replay) *Cursor {
	return &Cursor{location: loc, provider: provider, replay: replay}
}

func (c *Cursor) Location() Location { return c.location }

// ensureReplay lazily materialises a Replay at the cursor's location.
func (c *Cursor) ensureReplay(ctx context.Context) (*Replay, error) {
	if c.replay != nil {
		return c.replay, nil
	}
	if c.provider == nil {
		return nil, ErrNotImplemented
	}
	snap, err := c.provider.ClosestBeforeCall(ctx, c.location.ThreadID, c.location.FunctionCounts)
	if err != nil {
		return nil, err
	}
	rp, err := snap.Replay(ctx)
	if err != nil {
		return nil, err
	}
	if _, err := rp.RunToCursor(ctx, c.location.RawCursor()); err != nil {
		rp.Close()
		return nil, err
	}
	c.replay = rp
	return c.replay, nil
}

// takeReplay steals the cached replay, clearing the cache.
func (c *Cursor) takeReplay() *Replay {
	rp := c.replay
	c.replay = nil
	return rp
}

// --- query methods (delegate through ensureReplay) ---

func (c *Cursor) Stack(ctx context.Context) ([]map[string]any, error) {
	rp, err := c.ensureReplay(ctx)
	if err != nil {
		return nil, err
	}
	return rp.Stack(ctx)
}

func (c *Cursor) Locals(ctx context.Context) ([]map[string]any, error) {
	rp, err := c.ensureReplay(ctx)
	if err != nil {
		return nil, err
	}
	return rp.Locals(ctx)
}

func (c *Cursor) InstructionToLineno(ctx context.Context) (InstructionInfo, error) {
	rp, err := c.ensureReplay(ctx)
	if err != nil {
		return InstructionInfo{}, err
	}
	return rp.InstructionToLineno(ctx)
}

func (c *Cursor) SourceLocation(ctx context.Context) (map[string]any, error) {
	rp, err := c.ensureReplay(ctx)
	if err != nil {
		return nil, err
	}
	return rp.SourceLocation(ctx)
}

// --- forward navigation (steal cache, mutate, give to new Cursor) ---

// Next finds the next position on a different source line (DAP next / step over).
func (c *Cursor) Next(ctx context.Context) (*Cursor, error) {
	rp, err := c.ensureReplay(ctx)
	if err != nil {
		return nil, err
	}
	currentLine, err := sourceLine(ctx, rp)
	if err != nil {
		return nil, err
	}
	if currentLine == 0 {
		return nil, ErrNotImplemented
	}

	rp = c.takeReplay()
	if err := AdvanceTo(ctx, rp, DifferentLine(currentLine)); err != nil {
		if rp.Location().IsZero() {
			rp.Close()
			return nil, err
		}
		return NewCursor(rp.Location(), c.provider, rp), nil
	}
	return NewCursor(rp.Location(), c.provider, rp), nil
}

// StepInto enters the next child call from the current position (DAP stepIn).
// It increments the parent's call count and appends a zero for the child frame,
// so the target matches PY_START of the callee at exact depth.
func (c *Cursor) StepInto(ctx context.Context) (*Cursor, error) {
	loc := c.location
	if len(loc.FunctionCounts) == 0 {
		return nil, ErrNotImplemented
	}
	childCounts := make(FunctionCounts, len(loc.FunctionCounts)+1)
	copy(childCounts, loc.FunctionCounts)
	childCounts[len(loc.FunctionCounts)-1]++
	childCounts[len(loc.FunctionCounts)] = 0

	target := Location{ThreadID: loc.ThreadID, FunctionCounts: childCounts}

	rp := c.takeReplay()
	if rp == nil {
		snap, err := c.provider.ClosestBeforeCall(ctx, target.ThreadID, target.FunctionCounts)
		if err != nil {
			return nil, err
		}
		rp, err = snap.Replay(ctx)
		if err != nil {
			return nil, err
		}
	}

	if _, err := rp.RunToCursor(ctx, target.RawCursor()); err != nil {
		rp.Close()
		return nil, err
	}
	return NewCursor(rp.Location(), c.provider, rp), nil
}

// Return runs forward until the current function returns (DAP stepOut).
func (c *Cursor) Return(ctx context.Context) (*Cursor, error) {
	rp := c.takeReplay()

	if rp != nil {
		// Sync replay location from cursor to guarantee FunctionCounts
		// are populated even if the replay's internal location drifted.
		rp.location = c.location
		_, stopResult, err := rp.RunToReturn(ctx, nil)
		if err != nil || isStopFailure(stopResult.Reason) {
			rp.Close()
			rp = nil
		}
	}

	if rp == nil {
		snap, err := c.provider.ClosestBeforeCall(ctx, c.location.ThreadID, c.location.FunctionCounts)
		if err != nil {
			return nil, err
		}
		var rpErr error
		rp, rpErr = snap.Replay(ctx)
		if rpErr != nil {
			return nil, rpErr
		}
		if _, err := rp.RunToCursor(ctx, c.location.RawCursor()); err != nil {
			rp.Close()
			return nil, err
		}
		if _, stopResult, err := rp.RunToReturn(ctx, nil); err != nil {
			rp.Close()
			return nil, err
		} else if isStopFailure(stopResult.Reason) {
			rp.Close()
			return nil, ErrNotImplemented
		}
	}

	if _, err := rp.NextInstruction(ctx); err != nil {
		rp.Close()
		return nil, err
	}
	return NewCursor(rp.Location(), c.provider, rp), nil
}

// StepTowards advances one bytecode instruction toward target. If the
// instruction enters a child function not on the direct path to target,
// it automatically exits via RunToReturn and returns the parent-frame
// position instead. Returns the target cursor itself when the journey
// is complete (FC match), transferring the replay cache if the target
// lacks one.
func (c *Cursor) StepTowards(ctx context.Context, target *Cursor) (*Cursor, error) {
	cFC := c.location.FunctionCounts
	tFC := target.location.FunctionCounts

	cmp := cFC.Compare(tFC)
	if cmp == 0 && c.location.Equal(target.location) {
		log.Printf("StepTowards: already at target fc=%v flasti=%v", cFC, c.location.FLasti)
		if target.replay == nil {
			target.replay = c.takeReplay()
		} else if c.replay != nil {
			c.replay.Close()
			c.replay = nil
		}
		return target, nil
	}
	if cmp > 0 {
		return nil, fmt.Errorf("StepTowards: target is behind current position")
	}

	log.Printf("StepTowards: fc=%v -> target fc=%v", cFC, tFC)

	rp, err := c.ensureReplay(ctx)
	if err != nil {
		return nil, err
	}
	rp = c.takeReplay()

	prevDepth := len(cFC)
	if _, err := rp.NextInstruction(ctx); err != nil {
		rp.Close()
		return nil, err
	}

	newFC := rp.Location().FunctionCounts
	log.Printf("StepTowards: NextInstruction fc=%v (depth %d->%d)", newFC, prevDepth, len(newFC))

	if len(newFC) > prevDepth {
		parentIdx := prevDepth - 1
		onPath := len(tFC) > prevDepth && parentIdx >= 0 && tFC[parentIdx] == newFC[parentIdx]
		if !onPath {
			log.Printf("StepTowards: wrong child at depth %d (fc[%d]=%d, target fc[%d]=%d), exiting",
				len(newFC), parentIdx, newFC[parentIdx], parentIdx, tFC[parentIdx])
			if _, _, err := rp.RunToReturn(ctx, nil); err != nil {
				rp.Close()
				return nil, err
			}
			if _, err := rp.NextInstruction(ctx); err != nil {
				rp.Close()
				return nil, err
			}
			log.Printf("StepTowards: back in parent fc=%v", rp.Location().FunctionCounts)
		} else {
			log.Printf("StepTowards: on path, entered child")
		}
	}

	if rp.Location().Equal(target.location) {
		log.Printf("StepTowards: reached target fc=%v flasti=%v", rp.Location().FunctionCounts, rp.Location().FLasti)
		if target.replay == nil {
			target.replay = rp
		} else {
			rp.Close()
		}
		return target, nil
	}
	return NewCursor(rp.Location(), c.provider, rp), nil
}

// StepsTo collects every intermediate cursor position from c to target
// by repeatedly calling StepTowards. The result is inclusive of c and
// exclusive of target.
func (c *Cursor) StepsTo(ctx context.Context, target *Cursor) ([]*Cursor, error) {
	log.Printf("StepsTo: from fc=%v to fc=%v", c.location.FunctionCounts, target.location.FunctionCounts)
	var steps []*Cursor
	cur := c
	for {
		steps = append(steps, cur)
		next, err := cur.StepTowards(ctx, target)
		if err != nil {
			log.Printf("StepsTo: error after %d steps: %v", len(steps), err)
			return steps, err
		}
		if next == target {
			log.Printf("StepsTo: reached target in %d steps", len(steps))
			return steps, nil
		}
		cur = next
	}
}

// BackwardsSteps returns a lazy sequence of cursors at every bytecode
// instruction in reverse execution order, starting from c toward program
// start. It uses Decreasing() for FC boundary scaffolding and StepsTo +
// reverse to fill in each chunk of bytecode instructions between boundaries.
func (c *Cursor) BackwardsSteps(ctx context.Context) iter.Seq[*Cursor] {
	return func(yield func(*Cursor) bool) {
		log.Printf("BackwardsSteps: starting from fc=%v", c.location.FunctionCounts)
		if !yield(c) {
			return
		}
		lastCursor := c
		first := true
		chunkIdx := 0
		for fc := range c.location.FunctionCounts.Decreasing() {
			if first {
				first = false
				continue
			}
			log.Printf("BackwardsSteps: chunk %d, boundary fc=%v", chunkIdx, fc)
			newCursor, err := c.Returned(ctx, fc)
			if err != nil {
				log.Printf("BackwardsSteps: Returned failed for fc=%v: %v", fc, err)
				return
			}
			steps, err := newCursor.StepsTo(ctx, lastCursor)
			if err != nil {
				log.Printf("BackwardsSteps: StepsTo failed for chunk %d: %v", chunkIdx, err)
				return
			}
			log.Printf("BackwardsSteps: chunk %d has %d steps, yielding reversed", chunkIdx, len(steps))
			for i := len(steps) - 1; i >= 0; i-- {
				if !yield(steps[i]) {
					return
				}
			}
			lastCursor = newCursor
			chunkIdx++
		}
		log.Printf("BackwardsSteps: exhausted after %d chunks", chunkIdx)
	}
}

// --- backward navigation (fresh replay from provider) ---

// Returned replays to the position described by fc.  fc is expected to
// represent a point in a parent frame just after a child call returned
// (e.g. the result of PreviousCall).  Only RunToCursor is needed
// because fc already identifies the post-return position in the parent.
func (c *Cursor) Returned(ctx context.Context, fc FunctionCounts) (*Cursor, error) {
	log.Printf("Returned: fc=%v", fc)
	snap, err := c.provider.ClosestBeforeCall(ctx, c.location.ThreadID, fc)
	if err != nil {
		return nil, err
	}
	rp, err := snap.Replay(ctx)
	if err != nil {
		return nil, err
	}
	cursor := RawCursor{ThreadID: c.location.ThreadID, FunctionCounts: fc}
	if _, err := rp.RunToCursor(ctx, cursor); err != nil {
		rp.Close()
		return nil, err
	}
	return NewCursor(rp.Location(), c.provider, rp), nil
}

// PreviousReturned steps back to the position just after the previous
// child call returned. It computes PreviousCall on the current
// FunctionCounts and delegates to Returned.
func (c *Cursor) PreviousReturned(ctx context.Context) (*Cursor, error) {
	modCounts, ok := c.location.FunctionCounts.PreviousCall()
	if !ok {
		return nil, ErrNotImplemented
	}

	log.Printf("PreviousReturned: from fc=%v, target fc=%v",
		c.location.FunctionCounts, modCounts)

	result, err := c.Returned(ctx, modCounts)
	if err != nil {
		return nil, err
	}

	if !result.Location().FunctionCounts.Before(c.location.FunctionCounts) {
		log.Printf("PreviousReturned: ASSERTION FAILED: result fc=%v >= input fc=%v",
			result.Location().FunctionCounts, c.location.FunctionCounts)
		return nil, fmt.Errorf("PreviousReturned: result not before input")
	}

	return result, nil
}

// PreviousStatement finds the first instruction of the previous source line
// (DAP stepBack). Walks backward through every bytecode instruction via
// BackwardsSteps, finds where the line changes, then continues to the
// first instruction of that line.
func (c *Cursor) PreviousStatement(ctx context.Context) (*Cursor, error) {
	loc := c.location

	rp, err := c.ensureReplay(ctx)
	if err != nil {
		return nil, err
	}
	currentLine, err := sourceLine(ctx, rp)
	if err != nil || currentLine == 0 {
		return nil, ErrNotImplemented
	}
	log.Printf("PreviousStatement: start fc=%v flasti=%v line=%d",
		loc.FunctionCounts, loc.FLasti, currentLine)

	// Sequential fast path: DISABLED for now — needs a way to know
	// whether FunctionCounts changed within the sequential window
	// (any opcode can dispatch a child call via dunder methods).
	// TODO: re-enable once we have runtime FC-aware gating.
	if false && loc.FLasti != nil {
		info, infoErr := rp.InstructionToLineno(ctx)
		if infoErr == nil && len(info.SequentialBefore) > 0 {
			curIdx := *loc.FLasti / 2
			if curIdx >= 0 && curIdx < len(info.SequentialBefore) {
				seqBefore := info.SequentialBefore[curIdx]
				if seqBefore > 0 {
					minIdx := curIdx - seqBefore
					targetIdx := -1
					for i := curIdx - 1; i >= minIdx; i-- {
						if info.Linenos[i] != currentLine && info.Linenos[i] != 0 && info.SequentialBefore[i] > 0 {
							targetIdx = i
							break
						}
					}
					if targetIdx >= 0 {
						targetFLasti := targetIdx * 2
						targetLoc := Location{
							ThreadID:       loc.ThreadID,
							FunctionCounts: loc.FunctionCounts,
							FLasti:         &targetFLasti,
							Lineno:         info.Linenos[targetIdx],
							MessageIndex:   loc.MessageIndex,
						}
						log.Printf("PreviousStatement: fast path fc=%v flasti=%d->%d line=%d->%d",
							loc.FunctionCounts, *loc.FLasti, targetFLasti, currentLine, info.Linenos[targetIdx])
						c.replay = nil
						rp.Close()
						return NewCursor(targetLoc, c.provider, nil), nil
					}
				}
			}
		}
	}

	// Walk backward instruction by instruction.
	// Phase 1: skip past instructions still on currentLine.
	// Phase 2: find the first instruction of the new line P.
	var prevLine int
	var result *Cursor
	first := true
	for cur := range c.BackwardsSteps(ctx) {
		if first {
			first = false
			continue
		}
		curRp, err := cur.ensureReplay(ctx)
		if err != nil {
			break
		}
		line, err := sourceLine(ctx, curRp)
		if err != nil || line == 0 {
			continue
		}
		if prevLine == 0 && line == currentLine {
			continue
		}
		if prevLine == 0 {
			prevLine = line
			result = cur
			continue
		}
		if line == prevLine {
			result = cur
			continue
		}
		break
	}
	if result == nil {
		return nil, ErrNotImplemented
	}
	log.Printf("PreviousStatement: result fc=%v flasti=%v line=%d",
		result.location.FunctionCounts, result.location.FLasti, prevLine)
	return result, nil
}

// StepBackInto steps back into the function identified by the current
// call counts, positioning at its return point.
func (c *Cursor) StepBackInto(ctx context.Context) (*Cursor, error) {
	loc := c.location
	if len(loc.FunctionCounts) == 0 {
		return nil, ErrNotImplemented
	}
	snap, err := c.provider.ClosestBeforeReturn(ctx, loc.ThreadID, loc.FunctionCounts)
	if err != nil {
		return nil, err
	}
	rp, err := snap.Replay(ctx)
	if err != nil {
		return nil, err
	}

	if _, err := rp.RunToCursor(ctx, Location{
		ThreadID:       loc.ThreadID,
		FunctionCounts: loc.FunctionCounts,
	}.RawCursor()); err != nil {
		rp.Close()
		return nil, err
	}
	if _, _, err := rp.RunToReturn(ctx, nil); err != nil {
		rp.Close()
		return nil, err
	}
	return NewCursor(rp.Location(), c.provider, rp), nil
}

// --- predicates and helpers ---

// ReplayPredicate tests whether a Replay's current position matches
// some condition.
type ReplayPredicate func(ctx context.Context, rp *Replay) (bool, error)

// DifferentLine returns a predicate that matches when the source line
// differs from the given line.
func DifferentLine(fromLine int) ReplayPredicate {
	return func(ctx context.Context, rp *Replay) (bool, error) {
		line, err := sourceLine(ctx, rp)
		if err != nil {
			return false, err
		}
		return line != 0 && line != fromLine, nil
	}
}

// AdvanceToNextFrameInstruction moves rp forward by one instruction
// within the current frame, mutating it in place.
func AdvanceToNextFrameInstruction(ctx context.Context, rp *Replay) error {
	baseDepth := len(rp.Location().FunctionCounts)
	if _, err := rp.NextInstruction(ctx); err != nil {
		return err
	}
	for len(rp.Location().FunctionCounts) > baseDepth {
		if _, _, err := rp.RunToReturn(ctx, nil); err != nil {
			return err
		}
		if _, err := rp.NextInstruction(ctx); err != nil {
			return err
		}
	}
	if len(rp.Location().FunctionCounts) < baseDepth {
		return fmt.Errorf("function returned: %w", ErrNotImplemented)
	}
	return nil
}

// NextFrameInstruction returns a new Replay positioned at the next
// instruction in the current frame. The original rp is not modified.
func NextFrameInstruction(ctx context.Context, rp *Replay) (*Replay, error) {
	f, err := rp.fork(ctx)
	if err != nil {
		return nil, fmt.Errorf("fork: %w", err)
	}
	if err := AdvanceToNextFrameInstruction(ctx, f); err != nil {
		f.Close()
		return nil, err
	}
	return f, nil
}

// AdvanceTo moves rp forward through the current frame until pred
// returns true, mutating rp in place.
func AdvanceTo(ctx context.Context, rp *Replay, pred ReplayPredicate) error {
	for {
		if err := AdvanceToNextFrameInstruction(ctx, rp); err != nil {
			return ErrNotImplemented
		}
		match, err := pred(ctx, rp)
		if err != nil {
			return err
		}
		if match {
			return nil
		}
	}
}

// FindInstruction forks rp and advances through the current frame's
// instructions until pred returns true. The original rp is not modified.
func FindInstruction(ctx context.Context, rp *Replay, pred ReplayPredicate) (*Replay, error) {
	f, err := rp.fork(ctx)
	if err != nil {
		return nil, fmt.Errorf("fork: %w", err)
	}
	if err := AdvanceTo(ctx, f, pred); err != nil {
		f.Close()
		return nil, err
	}
	return f, nil
}

// --- sequence helpers ---

// decliningCallCounts yields call counts with the last element
// decremented on each step.
func decliningCallCounts(cc FunctionCounts) iter.Seq[FunctionCounts] {
	return func(yield func(FunctionCounts) bool) {
		if len(cc) == 0 {
			return
		}
		base := cc[:len(cc)-1]
		for i := cc[len(cc)-1] - 1; i >= 0; i-- {
			out := make(FunctionCounts, len(base)+1)
			copy(out, base)
			out[len(base)] = i
			if !yield(out) {
				return
			}
		}
	}
}

// sourceLine returns the source line for a Replay's current position,
// or 0 if unresolvable.
func sourceLine(ctx context.Context, rp *Replay) (int, error) {
	loc := rp.Location()
	if loc.Lineno > 0 {
		return loc.Lineno, nil
	}
	if loc.FLasti != nil {
		info, err := rp.InstructionToLineno(ctx)
		if err == nil {
			idx := *loc.FLasti / 2
			if idx < len(info.Linenos) && info.Linenos[idx] > 0 {
				return info.Linenos[idx], nil
			}
		}
	}
	sl, err := rp.SourceLocation(ctx)
	if err != nil {
		return 0, nil
	}
	if line, ok := sl["line"].(float64); ok && line > 0 {
		return int(line), nil
	}
	return 0, nil
}

// locationLine resolves a Location's source line using a lineno table
// from an existing Replay (same code object).
func locationLine(l Location, ctx context.Context, rp *Replay) (int, error) {
	if l.FLasti == nil {
		return 0, nil
	}
	info, err := rp.InstructionToLineno(ctx)
	if err != nil {
		return 0, err
	}
	idx := *l.FLasti / 2
	if idx >= len(info.Linenos) {
		return 0, nil
	}
	return info.Linenos[idx], nil
}

// --- JSON ---

func (c *Cursor) MarshalJSON() ([]byte, error) {
	return json.Marshal(c.location)
}

func (c *Cursor) UnmarshalJSON(data []byte) error {
	return json.Unmarshal(data, &c.location)
}
