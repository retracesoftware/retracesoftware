package replay

import "testing"

func TestHitListOrdersEqualMessageIndexByExecutionCursor(t *testing.T) {
	mainHit := BreakpointHit{
		BreakpointID: 1,
		Spec:         BreakpointSpec{File: "main.py", Line: 9},
		Location: Location{
			MessageIndex:   1487,
			FunctionCounts: FunctionCounts{1, 14, 1, 5, 5, 1, 1, 3, 0},
		},
	}
	serviceHit := BreakpointHit{
		BreakpointID: 2,
		Spec:         BreakpointSpec{File: "service.py", Line: 17},
		Location: Location{
			MessageIndex:   1487,
			FunctionCounts: FunctionCounts{1, 14, 1, 5, 5, 1, 1, 3, 2, 2, 0},
		},
	}

	hits := NewHitList()
	hits.Insert(serviceHit)
	hits.Insert(mainHit)

	got, ok := hits.FirstFrom(0)
	if !ok {
		t.Fatal("FirstFrom returned no hit")
	}
	if got.Spec.File != "main.py" {
		t.Fatalf("first hit = %s, want main.py", got.Spec.File)
	}
}

func TestHitListOrdersEqualCursorByFLasti(t *testing.T) {
	firstOffset := 44
	secondOffset := 54
	first := BreakpointHit{
		BreakpointID: 1,
		Location: Location{
			MessageIndex:   1487,
			FunctionCounts: FunctionCounts{1, 2, 3},
			FLasti:         &firstOffset,
		},
	}
	second := BreakpointHit{
		BreakpointID: 2,
		Location: Location{
			MessageIndex:   1487,
			FunctionCounts: FunctionCounts{1, 2, 3},
			FLasti:         &secondOffset,
		},
	}

	hits := NewHitList()
	hits.Insert(second)
	hits.Insert(first)

	got, ok := hits.FirstFrom(0)
	if !ok {
		t.Fatal("FirstFrom returned no hit")
	}
	if got.BreakpointID != 1 {
		t.Fatalf("first hit id = %d, want 1", got.BreakpointID)
	}
}
