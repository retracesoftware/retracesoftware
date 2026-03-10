package replay

import "context"

// Snapshot is a reusable checkpoint in a recorded trace. It knows its
// per-thread positions and can produce a live *Replay via fork.
type Snapshot struct {
	positions map[uint64][]int // threadID -> callCounts
	source    *Replay
}

func NewSnapshot(positions map[uint64][]int, source *Replay) *Snapshot {
	return &Snapshot{positions: positions, source: source}
}

// Positions returns the per-thread call counts for this checkpoint.
func (s *Snapshot) Positions() map[uint64][]int { return s.positions }

// Replay forks the internal checkpointed process and returns a fresh
// live replay at this snapshot's position. The snapshot retains its
// original for future forks.
func (s *Snapshot) Replay(ctx context.Context) (*Replay, error) {
	return s.source.fork(ctx)
}

// SnapshotProvider manages a pool of Snapshots and finds the closest
// one before a target call or return position.
type SnapshotProvider interface {
	ClosestBeforeCall(ctx context.Context, threadID uint64, callCounts FunctionCounts) (*Snapshot, error)
	ClosestBeforeReturn(ctx context.Context, threadID uint64, callCounts FunctionCounts) (*Snapshot, error)
}

// SimpleSnapshotProvider is a SnapshotProvider seeded with a single
// root snapshot. More sophisticated strategies can be layered on later.
type SimpleSnapshotProvider struct {
	snapshots []*Snapshot
}

func NewSimpleSnapshotProvider(root *Replay) *SimpleSnapshotProvider {
	return &SimpleSnapshotProvider{
		snapshots: []*Snapshot{NewSnapshot(map[uint64][]int{}, root)},
	}
}

func (p *SimpleSnapshotProvider) ClosestBeforeCall(_ context.Context, _ uint64, _ FunctionCounts) (*Snapshot, error) {
	return p.snapshots[0], nil
}

func (p *SimpleSnapshotProvider) ClosestBeforeReturn(_ context.Context, _ uint64, _ FunctionCounts) (*Snapshot, error) {
	return p.snapshots[0], nil
}
