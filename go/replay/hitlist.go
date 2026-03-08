package replay

import (
	"sort"
	"sync"
)

// HitList is a thread-safe sorted collection of BreakpointHits ordered by
// the Location's MessageIndex. Hits stream in asynchronously from background
// replays and are inserted in sorted position.
type HitList struct {
	mu   sync.Mutex
	hits []BreakpointHit
}

func NewHitList() *HitList {
	return &HitList{}
}

// Insert adds a hit in sorted order (by Location.MessageIndex).
func (h *HitList) Insert(hit BreakpointHit) {
	h.mu.Lock()
	defer h.mu.Unlock()

	mi := hit.Location.MessageIndex
	i := sort.Search(len(h.hits), func(j int) bool {
		return h.hits[j].Location.MessageIndex > mi
	})
	h.hits = append(h.hits, BreakpointHit{})
	copy(h.hits[i+1:], h.hits[i:])
	h.hits[i] = hit
}

// RemoveByBreakpoint removes all hits belonging to the given breakpoint ID.
func (h *HitList) RemoveByBreakpoint(id int) {
	h.mu.Lock()
	defer h.mu.Unlock()

	n := 0
	for _, hit := range h.hits {
		if hit.BreakpointID != id {
			h.hits[n] = hit
			n++
		}
	}
	h.hits = h.hits[:n]
}

func (h *HitList) Len() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.hits)
}

func (h *HitList) At(i int) BreakpointHit {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.hits[i]
}

// FirstFrom returns the first hit with MessageIndex >= the given value.
func (h *HitList) FirstFrom(messageIndex uint64) (BreakpointHit, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()

	i := sort.Search(len(h.hits), func(j int) bool {
		return h.hits[j].Location.MessageIndex >= messageIndex
	})
	if i >= len(h.hits) {
		return BreakpointHit{}, false
	}
	return h.hits[i], true
}

// NextAfter returns the first hit with MessageIndex strictly greater than
// the given value. Returns false if no such hit exists.
func (h *HitList) NextAfter(messageIndex uint64) (BreakpointHit, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()

	i := sort.Search(len(h.hits), func(j int) bool {
		return h.hits[j].Location.MessageIndex > messageIndex
	})
	if i >= len(h.hits) {
		return BreakpointHit{}, false
	}
	return h.hits[i], true
}

// PrevBefore returns the last hit with MessageIndex strictly less than
// the given value. Returns false if no such hit exists.
func (h *HitList) PrevBefore(messageIndex uint64) (BreakpointHit, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()

	i := sort.Search(len(h.hits), func(j int) bool {
		return h.hits[j].Location.MessageIndex >= messageIndex
	})
	if i == 0 {
		return BreakpointHit{}, false
	}
	return h.hits[i-1], true
}

// LastAtOrBefore returns the last hit with MessageIndex <= the given value.
// Returns false if no such hit exists.
func (h *HitList) LastAtOrBefore(messageIndex uint64) (BreakpointHit, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()

	i := sort.Search(len(h.hits), func(j int) bool {
		return h.hits[j].Location.MessageIndex > messageIndex
	})
	if i == 0 {
		return BreakpointHit{}, false
	}
	return h.hits[i-1], true
}
