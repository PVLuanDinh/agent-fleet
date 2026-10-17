package ui

import (
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// The wordmark is the product name set plainly, the way a native app titles
// its window: lowercase, bold, at the reading size, with no tracking or
// gradient to perform. A soft highlight in the theme's accent passes over
// it on launch and once in a while after. One row: a title, not a splash.
const (
	bannerWord = "agent-fleet"
	bannerRows = 1

	// bannerFrames is how long a sweep runs. Between sweeps the wordmark
	// holds still: a permanently animating header would repaint the frame
	// forever, and a repainting frame cannot be selected with the mouse.
	bannerFrames   = 22
	bannerInterval = 110 * time.Millisecond
	// bannerShimmerEvery re-runs the sweep on a settled wordmark.
	bannerShimmerEvery = 30 * time.Second
)

type (
	bannerTickMsg    struct{}
	bannerShimmerMsg struct{}
)

// bannerTick drives a sweep frame by frame; a settled wordmark waits out
// the shimmer interval before the next one.
func (m *Model) bannerTick() tea.Cmd {
	if m.bannerPhase >= bannerFrames {
		return tea.Tick(bannerShimmerEvery, func(time.Time) tea.Msg { return bannerShimmerMsg{} })
	}
	return tea.Tick(bannerInterval, func(time.Time) tea.Msg { return bannerTickMsg{} })
}

// bannerWidth is the columns the wordmark needs.
func bannerWidth() int {
	return len(bannerWord)
}

// showBanner reports whether the terminal leaves the lit wordmark room
// beside the fleet readings; a narrow one gets the name in the accent alone.
func (m *Model) showBanner() bool {
	return m.width >= bannerWidth()+railGutter+40
}

// headerRows is how many rows the header occupies, which the body height
// and every mouse hit-test are measured against.
func (m *Model) headerRows() int {
	if m.hideHeader {
		return 0
	}
	return bannerRows
}

// viewBanner draws the wordmark, lit by a highlight that sweeps left to
// right during the intro and then rests just past the end of the word.
func (m *Model) viewBanner() []string {
	sweep := float64(m.bannerPhase) / float64(bannerFrames)
	// The resting position keeps the tail of the word brightest, so a
	// settled header still has a direction to it.
	head := sweep * float64(bannerWidth()) * 1.25

	var b strings.Builder
	b.WriteString(strings.Repeat(" ", railGutter))
	if !m.showBanner() {
		// Too tight for tracking; the name still leads the rail.
		b.WriteString(lipgloss.NewStyle().Bold(true).Foreground(colorAccent).Render(bannerWord))
		return []string{b.String()}
	}
	for i, letter := range bannerWord {
		b.WriteString(lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color(bannerTint(float64(i), head))).
			Render(string(letter)))
	}
	return []string{b.String()}
}

// bannerRest is a column's settled color: the theme's brightest label, the
// same ink a native title bar uses, so the name never competes with state.
func bannerRest(float64) string {
	return current.Bright
}

// bannerTint colors one column of the wordmark by its distance from the
// sweep's head: tinted toward the accent at the head, settled behind it,
// dimmed ahead of it so the sweep reads as light passing over the word.
func bannerTint(column, head float64) string {
	rest := bannerRest(column)
	distance := head - column
	switch {
	case distance < 0:
		return mix(rest, current.Subtle, 0.6)
	case distance < 3:
		return mix(rest, current.Accent, 0.7*(1-distance/3))
	default:
		return rest
	}
}
