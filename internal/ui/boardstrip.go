package ui

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/PVLuanDinh/agent-fleet/internal/status"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// The board strip puts the agent-fleet message board in the header: how many
// owner questions wait on you and how many files are claimed. The board is a
// directory (msgs/<seq>.json, claims/*.claim) named by $AGENT_FLEET_BOARD;
// without it the strip is absent and nothing here runs. Reads happen off the
// update path on a slow tick and only parse messages past the last one seen,
// so a board with thousands of messages costs one directory listing a tick.

const (
	boardEnv      = "AGENT_FLEET_BOARD"
	boardInterval = 15 * time.Second
)

type boardSummary struct {
	ok        bool
	head      int
	questions int
	claims    int
}

type boardScanMsg struct{ sum boardSummary }

// boardScanner keeps the incremental state between scans. Only one scan is
// ever in flight: the next tick is scheduled when the previous result lands.
type boardScanner struct {
	root string
	last int
	open map[int]bool
}

func newBoardScanner() *boardScanner {
	root := strings.TrimSpace(os.Getenv(boardEnv))
	if root == "" {
		return nil
	}
	return &boardScanner{root: root, open: map[int]bool{}}
}

func (s *boardScanner) scan() boardSummary {
	entries, err := os.ReadDir(filepath.Join(s.root, "msgs"))
	if err != nil {
		return boardSummary{}
	}
	var seqs []int
	for _, e := range entries {
		name := e.Name()
		if !strings.HasSuffix(name, ".json") {
			continue
		}
		if n, err := strconv.Atoi(strings.TrimSuffix(name, ".json")); err == nil && n > s.last {
			seqs = append(seqs, n)
		}
	}
	sort.Ints(seqs)
	for _, n := range seqs {
		data, err := os.ReadFile(filepath.Join(s.root, "msgs", fmt.Sprintf("%08d.json", n)))
		if err != nil {
			break // a message mid-write is read on the next tick
		}
		var m struct {
			Kind    string `json:"kind"`
			ReplyTo any    `json:"reply_to"`
		}
		if json.Unmarshal(data, &m) == nil {
			switch m.Kind {
			case "owner-question":
				s.open[n] = true
			case "owner-answer":
				if to, ok := replySeq(m.ReplyTo); ok {
					delete(s.open, to)
				}
			}
		}
		s.last = n
	}
	claims, _ := filepath.Glob(filepath.Join(s.root, "claims", "*.claim"))
	return boardSummary{ok: true, head: s.last, questions: len(s.open), claims: len(claims)}
}

// replySeq reads reply_to whether the board wrote it as a number or a string.
func replySeq(v any) (int, bool) {
	switch t := v.(type) {
	case float64:
		return int(t), true
	case string:
		n, err := strconv.Atoi(t)
		return n, err == nil
	}
	return 0, false
}

// boardScanCmd runs one scan off the update path.
func (m *Model) boardScanCmd() tea.Cmd {
	s := m.board
	if s == nil {
		return nil
	}
	return func() tea.Msg { return boardScanMsg{sum: s.scan()} }
}

func (m *Model) boardTick() tea.Cmd {
	if m.board == nil {
		return nil
	}
	return tea.Tick(boardInterval, func(time.Time) tea.Msg { return boardTickMsg{} })
}

type boardTickMsg struct{}

// headerBoard is the strip's header piece. Open owner questions take the
// waiting color, because they wait on you exactly the way a waiting agent
// does; claims stay quiet, they are context rather than a call to act.
func (m *Model) headerBoard(compact bool) string {
	sum := m.boardSum
	if !sum.ok {
		return ""
	}
	q := subtleStyle.Render("no questions")
	if sum.questions > 0 {
		word := " questions"
		if sum.questions == 1 {
			word = " question"
		}
		if compact {
			word = "?"
		}
		q = lipgloss.NewStyle().Foreground(colorWaiting).Bold(true).Render(statusGlyph(status.Waiting)+" "+strconv.Itoa(sum.questions)) +
			lipgloss.NewStyle().Foreground(colorWaiting).Render(word)
	} else if compact {
		q = ""
	}
	// Claims are context, not a call to act: the compact strip drops them
	// and keeps only the questions that wait on you.
	claims := valueStyle.Render(strconv.Itoa(sum.claims)) + subtleStyle.Render(" claims")
	if compact {
		if q == "" {
			return ""
		}
		claims = ""
	}
	label := labelStyle.Render("board ")
	return label + joinHeaderPieces(subtleStyle.Render(" · "), q, claims)
}
