package ui

import "testing"

// A settled wordmark keeps a timer alive for the next shimmer, and the
// shimmer message starts the sweep over.
func TestBannerShimmerReArms(t *testing.T) {
	m := &Model{bannerPhase: bannerFrames}
	if m.bannerTick() == nil {
		t.Fatal("a settled banner should wait for the next shimmer, not stop")
	}
	updated, cmd := m.Update(bannerShimmerMsg{})
	m = updated.(*Model)
	if m.bannerPhase != 0 {
		t.Fatalf("shimmer should restart the sweep, phase = %d", m.bannerPhase)
	}
	if cmd == nil {
		t.Fatal("a restarted sweep must schedule its frames")
	}
}

// The resting wordmark is set in the theme's brightest label across its
// whole width, and the sweep's head leans it toward the accent.
func TestBannerRestIsBrightAndSweepTintsAccent(t *testing.T) {
	if got := bannerRest(0); got != current.Bright {
		t.Fatalf("rest = %s, want the theme's bright label %s", got, current.Bright)
	}
	if bannerRest(0) != bannerRest(float64(bannerWidth()-1)) {
		t.Fatal("a plain wordmark has one resting color end to end")
	}
	if lit := bannerTint(3, 3); lit == current.Bright {
		t.Fatal("the column under the sweep head should be tinted toward the accent")
	}
	if bannerWord != "agent-fleet" {
		t.Fatalf("wordmark = %q, want agent-fleet", bannerWord)
	}
}
