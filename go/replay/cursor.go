package replay

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"iter"
	"log"
)

var ErrNotImplemented = errors.New("not implemented")

func isStopFailure(reason string) bool {
	return reason == "eof" || reason == "overshoot"
}

// Coordinates is retrace-python's visible frame coordinate stack.
// Each frame contributes a (call_ordinal, instruction_coordinate) pair,
// oldest visible frame first.
type Coordinates []int

// Depth returns the call-stack depth (number of frames).
func (coords Coordinates) Depth() int { return len(coords) / 2 }

// Parent returns the coordinates with the innermost frame removed.
// Returns nil for an empty path.
func (coords Coordinates) Parent() Coordinates {
	if len(coords) < 2 {
		return nil
	}
	out := make(Coordinates, len(coords)-2)
	copy(out, coords[:len(coords)-2])
	return out
}

// Compare returns -1 if coords is earlier in execution than other, +1 if
// later, and 0 if equal. This mirrors retrace-python's call_at ordering:
// lexicographic on the shared prefix, with a shorter equal prefix treated as
// earlier than the deeper coordinate stack.
func (coords Coordinates) Compare(other Coordinates) int {
	n := len(coords)
	if len(other) < n {
		n = len(other)
	}
	for i := 0; i < n; i++ {
		if coords[i] < other[i] {
			return -1
		}
		if coords[i] > other[i] {
			return 1
		}
	}
	if len(coords) > len(other) {
		return 1
	}
	if len(coords) < len(other) {
		return -1
	}
	return 0
}

// Before reports whether coords is strictly earlier in execution than other.
func (coords Coordinates) Before(other Coordinates) bool {
	return coords.Compare(other) < 0
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

// Location is a pure-data position within a recorded trace. It holds the
// thread ID, per-frame coordinates, optional bytecode offset, and message
// index. It is cheap to copy and safe to store in bulk (e.g. HitList).
type Location struct {
	ThreadID     uint64      `json:"thread_id"`
	Coordinates  Coordinates `json:"coordinates"`
	FLasti       *int        `json:"f_lasti,omitempty"`
	Lineno       int         `json:"lineno,omitempty"`
	MessageIndex uint64      `json:"message_index"`
}

func (l Location) Equal(other Location) bool {
	if l.ThreadID != other.ThreadID {
		return false
	}
	if l.Coordinates.Compare(other.Coordinates) != 0 {
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
	return l.ThreadID == 0 && len(l.Coordinates) == 0 && l.MessageIndex == 0
}

func (l Location) RawCursor() RawCursor {
	return RawCursor{
		ThreadID:    l.ThreadID,
		Coordinates: l.Coordinates,
		FLasti:      l.FLasti,
		Lineno:      l.Lineno,
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
		c.replay.location = c.location
		return c.replay, nil
	}
	if c.provider == nil {
		return nil, ErrNotImplemented
	}
	snap, err := c.provider.ClosestBeforeCall(ctx, c.location.ThreadID, c.location.Coordinates)
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

// StepInto advances along the recorded execution until it either enters a
// child frame or reaches a different source line. It must follow the live
// replay instead of synthesizing a child Coordinates path: not every stop
// position is followed by a reachable child call.
func (c *Cursor) StepInto(ctx context.Context) (*Cursor, error) {
	rp, err := c.ensureReplay(ctx)
	if err != nil {
		return nil, err
	}

	currentLine, err := sourceLine(ctx, rp)
	if err != nil {
		return nil, err
	}

	rp = c.takeReplay()
	baseDepth := c.location.Coordinates.Depth()

	for {
		if _, err := rp.NextInstruction(ctx); err != nil {
			rp.Close()
			return nil, err
		}

		nextLoc := rp.Location()
		if nextLoc.Coordinates.Depth() != baseDepth {
			return NewCursor(nextLoc, c.provider, rp), nil
		}

		line, err := sourceLine(ctx, rp)
		if err != nil {
			rp.Close()
			return nil, err
		}
		if currentLine == 0 || (line != 0 && line != currentLine) {
			return NewCursor(nextLoc, c.provider, rp), nil
		}
	}
}

// Return runs forward until the current function returns (DAP stepOut).
func (c *Cursor) Return(ctx context.Context) (*Cursor, error) {
	rp := c.takeReplay()

	if rp != nil {
		// Sync replay location from cursor to guarantee Coordinates
		// are populated even if the replay's internal location drifted.
		rp.location = c.location
		_, stopResult, err := rp.RunToReturn(ctx)
		if err != nil || isStopFailure(stopResult.Reason) {
			rp.Close()
			rp = nil
		}
	}

	if rp == nil {
		snap, err := c.provider.ClosestBeforeCall(ctx, c.location.ThreadID, c.location.Coordinates)
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
		if _, stopResult, err := rp.RunToReturn(ctx); err != nil {
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
// is complete (coordinate match), transferring the replay cache if the target
// lacks one.
func (c *Cursor) StepTowards(ctx context.Context, target *Cursor) (*Cursor, error) {
	currentCoords := c.location.Coordinates
	targetCoords := target.location.Coordinates

	cmp := currentCoords.Compare(targetCoords)
	if cmp == 0 && c.location.Equal(target.location) {
		log.Printf("StepTowards: already at target coords=%v flasti=%v", currentCoords, c.location.FLasti)
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

	log.Printf("StepTowards: coords=%v -> target coords=%v", currentCoords, targetCoords)

	rp, err := c.ensureReplay(ctx)
	if err != nil {
		return nil, err
	}
	rp = c.takeReplay()

	prevDepth := currentCoords.Depth()
	if _, err := rp.NextInstruction(ctx); err != nil {
		rp.Close()
		return nil, err
	}

	newCoords := rp.Location().Coordinates
	newDepth := newCoords.Depth()
	log.Printf("StepTowards: NextInstruction coords=%v (depth %d->%d)", newCoords, prevDepth, newDepth)

	if newDepth > prevDepth {
		onPath := sameFramePathPrefix(newCoords, targetCoords)
		if !onPath {
			log.Printf("StepTowards: entered child off target path, exiting")
			if _, _, err := rp.RunToReturn(ctx); err != nil {
				rp.Close()
				return nil, err
			}
			if _, err := rp.NextInstruction(ctx); err != nil {
				rp.Close()
				return nil, err
			}
			log.Printf("StepTowards: back in parent coords=%v", rp.Location().Coordinates)
		} else {
			log.Printf("StepTowards: on path, entered child")
		}
	}

	if rp.Location().Equal(target.location) {
		log.Printf("StepTowards: reached target coords=%v flasti=%v", rp.Location().Coordinates, rp.Location().FLasti)
		if target.replay == nil {
			target.replay = rp
		} else {
			rp.Close()
		}
		return target, nil
	}
	return NewCursor(rp.Location(), c.provider, rp), nil
}

func sameFramePathPrefix(prefix Coordinates, coords Coordinates) bool {
	if len(prefix) > len(coords) || len(prefix)%2 != 0 || len(coords)%2 != 0 {
		return false
	}
	for i := 0; i < len(prefix); i += 2 {
		if prefix[i] != coords[i] {
			return false
		}
		if i+1 == len(prefix)-1 {
			continue
		}
		if prefix[i+1] != coords[i+1] {
			return false
		}
	}
	return true
}

// StepsTo collects every intermediate cursor position from c to target
// by repeatedly calling StepTowards. The result is inclusive of c and
// exclusive of target.
func (c *Cursor) StepsTo(ctx context.Context, target *Cursor) ([]*Cursor, error) {
	log.Printf("StepsTo: from coords=%v to coords=%v", c.location.Coordinates, target.location.Coordinates)
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

// BackwardsSteps returns the current cursor only. Retrace-python coordinates
// are runtime-owned, so the Go layer cannot synthesize earlier coordinate
// stacks by decrementing integers.
func (c *Cursor) BackwardsSteps(ctx context.Context) iter.Seq[*Cursor] {
	return func(yield func(*Cursor) bool) {
		_ = ctx
		log.Printf("BackwardsSteps: starting from coords=%v", c.location.Coordinates)
		yield(c)
	}
}

// --- backward navigation (fresh replay from provider) ---

// Returned replays to the exact coordinate stack described by coords.
func (c *Cursor) Returned(ctx context.Context, coords Coordinates) (*Cursor, error) {
	log.Printf("Returned: coords=%v", coords)
	snap, err := c.provider.ClosestBeforeCall(ctx, c.location.ThreadID, coords)
	if err != nil {
		return nil, err
	}
	rp, err := snap.Replay(ctx)
	if err != nil {
		return nil, err
	}
	cursor := RawCursor{ThreadID: c.location.ThreadID, Coordinates: coords}
	if _, err := rp.RunToCursor(ctx, cursor); err != nil {
		rp.Close()
		return nil, err
	}
	return NewCursor(rp.Location(), c.provider, rp), nil
}

// PreviousReturned is not available from coordinates alone. Earlier valid
// coordinates must come from replay, not from local integer arithmetic.
func (c *Cursor) PreviousReturned(ctx context.Context) (*Cursor, error) {
	_ = ctx
	return nil, ErrNotImplemented
}

// PreviousStatement finds the first instruction of the previous source line
// (DAP stepBack). The same-frame fast path uses f_lasti metadata; crossing
// call boundaries is unavailable without a replay-provided prior coordinate.
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
	log.Printf("PreviousStatement: start coords=%v flasti=%v line=%d",
		loc.Coordinates, loc.FLasti, currentLine)

	// Same-frame fast path. This handles the common reverse-step-over case:
	// the current line is in the same code object as the previous source line,
	// but the slow coordinate walk may not have a valid boundary to land on.
	if loc.FLasti != nil {
		info, infoErr := rp.InstructionToLineno(ctx)
		if infoErr == nil {
			curIdx := *loc.FLasti / 2
			if curIdx >= 0 && curIdx < len(info.Linenos) {
				minIdx := 0
				if curIdx < len(info.SequentialBefore) {
					minIdx = curIdx - info.SequentialBefore[curIdx]
				}
				targetIdx := -1
				targetLine := 0
				for i := curIdx - 1; i >= minIdx; i-- {
					line := info.Linenos[i]
					if line == 0 || line == currentLine {
						continue
					}
					targetIdx = i
					targetLine = line
					break
				}
				for targetIdx > minIdx && info.Linenos[targetIdx-1] == targetLine {
					targetIdx--
				}
				if targetIdx >= 0 {
					targetFLasti := targetIdx * 2
					targetLoc := Location{
						ThreadID:     loc.ThreadID,
						Coordinates:  loc.Coordinates,
						FLasti:       &targetFLasti,
						Lineno:       targetLine,
						MessageIndex: loc.MessageIndex,
					}
					log.Printf("PreviousStatement: fast path coords=%v flasti=%d->%d line=%d->%d",
						loc.Coordinates, *loc.FLasti, targetFLasti, currentLine, targetLine)
					c.replay = nil
					rp.Close()
					return NewCursor(targetLoc, c.provider, nil), nil
				}
			}
		}
	}

	return nil, ErrNotImplemented
}

// StepBackInto steps back into the function identified by the current
// coordinates, positioning at its return point.
func (c *Cursor) StepBackInto(ctx context.Context) (*Cursor, error) {
	loc := c.location
	if loc.Coordinates.Depth() == 0 {
		return nil, ErrNotImplemented
	}
	snap, err := c.provider.ClosestBeforeReturn(ctx, loc.ThreadID, loc.Coordinates)
	if err != nil {
		return nil, err
	}
	rp, err := snap.Replay(ctx)
	if err != nil {
		return nil, err
	}

	if _, err := rp.RunToCursor(ctx, Location{
		ThreadID:    loc.ThreadID,
		Coordinates: loc.Coordinates,
	}.RawCursor()); err != nil {
		rp.Close()
		return nil, err
	}
	if _, _, err := rp.RunToReturn(ctx); err != nil {
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
	baseDepth := rp.Location().Coordinates.Depth()
	if _, err := rp.NextInstruction(ctx); err != nil {
		return err
	}
	for rp.Location().Coordinates.Depth() > baseDepth {
		if _, _, err := rp.RunToReturn(ctx); err != nil {
			return err
		}
		if _, err := rp.NextInstruction(ctx); err != nil {
			return err
		}
	}
	if rp.Location().Coordinates.Depth() < baseDepth {
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
