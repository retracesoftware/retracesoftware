package replay

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"iter"
)

var ErrNotImplemented = errors.New("not implemented")

// Location is a pure-data position within a recorded trace. It holds the
// thread ID, per-frame call counts, optional bytecode offset, and message
// index. It is cheap to copy and safe to store in bulk (e.g. HitList).
type Location struct {
	ThreadID       uint64 `json:"thread_id"`
	FunctionCounts []int  `json:"function_counts"`
	FLasti         *int   `json:"f_lasti,omitempty"`
	MessageIndex   uint64 `json:"message_index"`
}

func (l Location) IsZero() bool {
	return l.ThreadID == 0 && len(l.FunctionCounts) == 0 && l.MessageIndex == 0
}

func (l Location) RawCursor() RawCursor {
	return RawCursor{
		ThreadID:       l.ThreadID,
		FunctionCounts: l.FunctionCounts,
		FLasti:         l.FLasti,
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

func (c *Cursor) InstructionToLineno(ctx context.Context) ([]int, error) {
	rp, err := c.ensureReplay(ctx)
	if err != nil {
		return nil, err
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
	childCounts := make([]int, len(loc.FunctionCounts)+1)
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
		_, stopResult, err := rp.RunToReturn(ctx, nil)
		if err != nil || stopResult.Reason == "eof" {
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
		} else if stopResult.Reason == "eof" {
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

// --- backward navigation (fresh replay from provider) ---

// Previous finds the last position before the current one that is on a
// different source line (DAP stepBack).
func (c *Cursor) Previous(ctx context.Context) (*Cursor, error) {
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

	loc := c.location
	snap, err := c.provider.ClosestBeforeCall(ctx, loc.ThreadID, loc.FunctionCounts)
	if err != nil {
		return nil, err
	}
	entry, err := snap.Replay(ctx)
	if err != nil {
		return nil, err
	}

	entryCounts := make([]int, len(loc.FunctionCounts))
	copy(entryCounts, loc.FunctionCounts)
	entryCounts[len(entryCounts)-1] = 0

	stopResult, err := entry.RunToCursor(ctx, Location{
		ThreadID:       loc.ThreadID,
		FunctionCounts: entryCounts,
	}.RawCursor())
	if err != nil {
		entry.Close()
		return nil, err
	}
	if stopResult.Reason == "eof" {
		entry.Close()
		return nil, ErrNotImplemented
	}

	result, err := advanceUntilLine(ctx, entry, currentLine)
	if err != nil {
		return nil, err
	}
	line, err := sourceLine(ctx, result)
	if err != nil || line == 0 || line == currentLine {
		result.Close()
		return nil, ErrNotImplemented
	}
	return NewCursor(result.Location(), c.provider, result), nil
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
func decliningCallCounts(cc []int) iter.Seq[[]int] {
	return func(yield func([]int) bool) {
		if len(cc) == 0 {
			return
		}
		base := cc[:len(cc)-1]
		for i := cc[len(cc)-1] - 1; i >= 0; i-- {
			out := make([]int, len(base)+1)
			copy(out, base)
			out[len(base)] = i
			if !yield(out) {
				return
			}
		}
	}
}

// advanceUntilLine walks forward from rp through the current frame,
// returning the last position before the source line becomes targetLine.
func advanceUntilLine(ctx context.Context, rp *Replay, targetLine int) (*Replay, error) {
	for {
		next, err := NextFrameInstruction(ctx, rp)
		if err != nil {
			return rp, nil
		}
		line, err := sourceLine(ctx, next)
		if err != nil {
			next.Close()
			return rp, nil
		}
		if line == targetLine {
			next.Close()
			return rp, nil
		}
		rp.Close()
		rp = next
	}
}

// sourceLine returns the source line for a Replay's current position,
// or 0 if unresolvable.
func sourceLine(ctx context.Context, rp *Replay) (int, error) {
	loc := rp.Location()
	if loc.FLasti != nil {
		linenos, err := rp.InstructionToLineno(ctx)
		if err == nil {
			idx := *loc.FLasti / 2
			if idx < len(linenos) && linenos[idx] > 0 {
				return linenos[idx], nil
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
	linenos, err := rp.InstructionToLineno(ctx)
	if err != nil {
		return 0, err
	}
	idx := *l.FLasti / 2
	if idx >= len(linenos) {
		return 0, nil
	}
	return linenos[idx], nil
}

// --- JSON ---

func (c *Cursor) MarshalJSON() ([]byte, error) {
	return json.Marshal(c.location)
}

func (c *Cursor) UnmarshalJSON(data []byte) error {
	return json.Unmarshal(data, &c.location)
}
