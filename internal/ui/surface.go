package ui

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

// The list view sits on the backdrop, with the sessions rail filling its
// pane flush — header rule to footer rule, window edge to seam — and the
// selected entry lifted once more. Views draw the backdrop as unpainted
// cells; fillBackdrop paints them, text color included, over the finished
// frame, and the terminal color sync keeps the window padding around the
// cell grid at the same tone. With the terminal background setting the
// cells stay unpainted, so a translucent window blends them exactly like
// its padding.

// backdropHex is the backdrop's fill while a view draws: none. paint treats
// it as "pad, but leave the terminal's background alone".
func backdropHex() string { return "" }

// panelHex is the rail's fill: one step above the backdrop.
func panelHex() string { return mix(current.Bg, current.Surface, 0.55) }

// blockHex is a section block inside the content area, quieter than the
// rail so it groups without announcing itself.
func blockHex() string { return mix(current.Bg, current.Surface, 0.35) }

// selectedHex is the band under the cursor's entry: the selection tone
// tinted toward the accent, the way a native source list marks the focused
// row, so the cursor reads as selection rather than as one more gray.
func selectedHex() string { return mix(current.Surface, current.Accent, 0.2) }

func quickModeHex() string { return panelHex() }

// searchFieldHex is the band under the open search field.
func searchFieldHex() string { return mix(panelHex(), current.Accent, 0.2) }

// liftedHex is the band under a row lifted for reordering: the selection
// tone pulled toward the accent, so the row reads as picked up.
func liftedHex() string { return mix(current.Surface, current.Accent, 0.45) }

// dropHex is the band under the row a drag would land on: the selection
// tone pulled toward the second accent, apart from the lifted row's.
func dropHex() string { return mix(current.Surface, current.Accent2, 0.3) }

// restingMarkHex is a row handle's and menu button's tone away from the
// cursor: present, but quieter than the row's text.
func restingMarkHex() string { return mix(current.Bg, current.Text, 0.3) }

// ruleHex is the hairline tone: lifted just far enough off the backdrop to
// draw a seam without becoming a border.
func ruleHex() string { return mix(current.Bg, current.Text, 0.22) }

// hrule is a horizontal seam across a painted row.
func hrule(width int) string {
	if width < 1 {
		return ""
	}
	return lipgloss.NewStyle().Foreground(lipgloss.Color(ruleHex())).Render(strings.Repeat("─", width))
}

// railTopRow opens the body under the title with one hairline, the way a
// native window separates its toolbar from its content: the rail keeps its
// own fill beneath the line, and the line runs unbroken across both sides.
func (m *Model) railTopRow(paneWidth, width int) string {
	return m.hairlineRow(paneWidth, width)
}

// boundedRuleRow closes the body with the same hairline that opened it, so
// the frame is two quiet lines rather than a drawn box. edge is kept for the
// callers that once picked a half block per side; the line is the same.
func (m *Model) boundedRuleRow(paneWidth, width int, edge string) string {
	return m.hairlineRow(paneWidth, width)
}

// hairlineRow is a full-width separator on the backdrop. The rail's fill
// starts on the row below it and stops on the row above the closing one, so
// the sidebar reads as sitting between the two lines, never poking past them.
func (m *Model) hairlineRow(paneWidth, width int) string {
	return paint(hrule(width), width, backdropHex())
}

// railEdgeCell is one row of the pane's first column, drawn as a foreground
// block in the row's own tone rather than as cell background. The window
// margin beside it inherits the cell's background — the terminal's own —
// so the pane's left edge lands exactly on the cell grid.
func railEdgeCell(tone string) string {
	return plain(lipgloss.NewStyle().Foreground(lipgloss.Color(tone)).Render("█"), 1)
}

// vruleColumn is the seam between two painted columns. It doubles as the
// resize grip, taking the accent while the divider is being moved.
func (m *Model) vruleColumn(height int) []string {
	lines := make([]string, height)
	for i := range lines {
		lines[i] = m.seamCell(false)
	}
	return lines
}

// bleedColumn finishes a pane's right edge: a single hairline on the
// backdrop, the sidebar separator of a native split view.
func (m *Model) bleedColumn(height int) []string {
	cell := paint(lipgloss.NewStyle().Foreground(lipgloss.Color(ruleHex())).Render("│"), 1, backdropHex())
	lines := make([]string, height)
	for i := range lines {
		lines[i] = cell
	}
	// Focus mode lights this column beside the pane rows alone, so the edge
	// traces the agent's terminal rather than the whole content panel. The
	// top corner sits on the rule row just above the capture.
	if m.mode == modeFocus && m.pane.box.ok {
		edge := paint(focusEdgeStyle.Render("│"), 1, backdropHex())
		corner := paint(focusEdgeStyle.Render("╭"), 1, backdropHex())
		top := m.pane.box.y - m.listChromeRows()
		if top-1 >= 0 && top-1 < len(lines) {
			lines[top-1] = corner
		}
		for row := top; row < top+m.pane.box.height; row++ {
			if row >= 0 && row < len(lines) {
				lines[row] = edge
			}
		}
	}
	return lines
}

// focusBottomRule closes the focused pane's hairline along the frame rule
// under the body: the corner cell is the bleed column the left edge runs
// down, so the two meet exactly.
func (m *Model) focusBottomRule(paneWidth, width int) string {
	tail := width - paneWidth - 1
	if tail < 1 || paneWidth < 2 {
		return m.boundedRuleRow(paneWidth, width, "▄")
	}
	corner := focusEdgeStyle.Render("╰")
	return plain(hrule(1), 1) + paint(hrule(paneWidth-1), paneWidth-1, panelHex()) +
		paint(corner, 1, backdropHex()) +
		paint(focusEdgeStyle.Render(strings.Repeat("─", tail)), tail, backdropHex())
}

// seamCell is one row of the vertical seam. The column is the pane's own
// fill, so only the pane's rules cross it: a content rule carried across
// reads as the content's separator running into the sessions list.
func (m *Model) seamCell(railRule bool) string {
	// While the divider is being moved the seam becomes the grip.
	if m.split.dragging || m.split.resizeMode {
		color := colorAccent
		if m.split.dragging {
			color = colorAccent2
		}
		return paint(lipgloss.NewStyle().Foreground(color).Render("║"), 1, panelHex())
	}
	if railRule {
		return paint(hrule(1), 1, panelHex())
	}
	return paint("", 1, panelHex())
}

// paint pads a possibly-styled line to an exact width and fills every cell
// with bg. Inner SGR resets emitted by per-segment renders would drop the
// fill partway across, so each reset re-applies it.
func paint(s string, width int, bg string) string {
	if bg == "" {
		return plain(s, width)
	}
	fill := bgSeq(bg)
	s = strings.ReplaceAll(s, "\x1b[0m", "\x1b[0m"+fill)
	if w := ansi.StringWidth(s); w > width {
		s = ansi.Truncate(s, width, "…")
	} else if w < width {
		s += strings.Repeat(" ", width-w)
	}
	return fill + s + "\x1b[0m"
}

// plain pads a line to width without filling it, leaving it on the backdrop.
// Captured agent output is drawn this way so a session's CLI looks exactly
// as it does inside the session.
func plain(s string, width int) string {
	if w := ansi.StringWidth(s); w > width {
		s = ansi.Truncate(s, width, "")
	} else if w < width {
		s += strings.Repeat(" ", width-w)
	}
	return "\x1b[0m" + s + "\x1b[0m"
}

// fillBackdrop paints the theme's background and text color into every
// cell the frame left on the terminal's own and pads each row to width, so
// the frame keeps its colors on a terminal that ignored the OSC 10 and 11
// sync.
func fillBackdrop(frame string, width int, backgroundHex, foregroundHex string) string {
	fill, ink := bgSeq(backgroundHex), fgSeq(foregroundHex)
	parser := ansi.GetParser()
	defer ansi.PutParser(parser)
	lines := strings.Split(frame, "\n")
	for i, line := range lines {
		lines[i] = fillBackdropLine(line, width, fill, ink, parser)
	}
	return strings.Join(lines, "\n")
}

func fillBackdropLine(line string, width int, fill, ink string, parser *ansi.Parser) string {
	var out strings.Builder
	out.WriteString(fill + ink)
	cells := 0
	var state byte
	for len(line) > 0 {
		seq, cellWidth, n, next := ansi.DecodeSequence(line, state, parser)
		out.WriteString(seq)
		if ansi.HasCsiPrefix(seq) && parser.Command() == 'm' {
			background, foreground := clearedColors(parser.Params())
			if background {
				out.WriteString(fill)
			}
			if foreground {
				out.WriteString(ink)
			}
		}
		cells += cellWidth
		line, state = line[n:], next
	}
	if cells < width {
		out.WriteString("\x1b[0m" + fill + ink + strings.Repeat(" ", width-cells))
	}
	out.WriteString("\x1b[0m")
	return out.String()
}

// clearedColors reports which of the terminal's own colors an SGR leaves
// showing once all of its parameters apply.
func clearedColors(params ansi.Params) (background, foreground bool) {
	if len(params) == 0 {
		return true, true
	}
	for i := 0; i < len(params); i += 1 + parameterArguments(params, i) {
		switch param, _, _ := params.Param(i, 0); {
		case param == 0:
			background, foreground = true, true
		case param == 49:
			background = true
		case param == 39:
			foreground = true
		case param >= 40 && param <= 48, param >= 100 && param <= 107:
			background = false
		case param >= 30 && param <= 38, param >= 90 && param <= 97:
			foreground = false
		}
	}
	return background, foreground
}

// parameterArguments counts the parameters that belong to the one at index
// at: its colon subparameters (4:3, 48:2::r:g:b), or the semicolon arguments
// of an extended color (38;5;n, 48;2;r;g;b, 58;5;n).
func parameterArguments(params ansi.Params, at int) int {
	count := 0
	for {
		_, subparameter, _ := params.Param(at+count, 0)
		if !subparameter {
			break
		}
		count++
	}
	if count > 0 {
		return count
	}
	switch param, _, _ := params.Param(at, 0); param {
	case 38, 48, 58:
		switch kind, _, _ := params.Param(at+1, 0); kind {
		case 5:
			return 2
		case 2:
			return 4
		}
	}
	return 0
}

// contentLine is one row of the content column: ours to paint, a seam that
// spans the column edge to edge, or captured output that must stay on the
// backdrop. Rail rows also carry the tone their fill uses, so the edge
// column beside them can match the selected entry's band.
type contentLine struct {
	text string
	raw  bool
	rule bool
	tone string
	// row is the m.rows index a click on this line selects, one-based so
	// a bare contentLine{} reads as chrome. Carrying it on the line is what
	// keeps hit-testing in step with paint through the prepends, the
	// truncation and the padding the rail's lines go through (#110).
	row int
}

// paintContent paints a content column, leaving raw rows unfilled.
func paintContent(lines []contentLine, width, height int, bg string) []string {
	out := make([]string, height)
	for i := 0; i < height; i++ {
		if i >= len(lines) {
			out[i] = paint("", width, bg)
			continue
		}
		if lines[i].rule {
			// Seams are drawn at the column's full width, not the inset the
			// text uses, so they meet the frame's edges exactly.
			out[i] = paint(hrule(width), width, bg)
			continue
		}
		if lines[i].raw {
			out[i] = plain(lines[i].text, width)
			continue
		}
		out[i] = paint(lines[i].text, width, bg)
	}
	return out
}

// paintRows paints each line of a block, padding the block itself out to
// height so a short column still fills its side of the frame.
func paintRows(lines []string, width, height int, bg string) []string {
	out := make([]string, height)
	for i := 0; i < height; i++ {
		content := ""
		if i < len(lines) {
			content = lines[i]
		}
		out[i] = paint(content, width, bg)
	}
	return out
}

// joinColumns stitches painted columns row by row.
func joinColumns(columns ...[]string) []string {
	height := 0
	for _, col := range columns {
		if len(col) > height {
			height = len(col)
		}
	}
	rows := make([]string, height)
	for i := 0; i < height; i++ {
		var b strings.Builder
		for _, col := range columns {
			if i < len(col) {
				b.WriteString(col[i])
			}
		}
		rows[i] = b.String()
	}
	return rows
}

// indentLines insets a block by n columns.
func indentLines(lines []string, n int) []string {
	pad := strings.Repeat(" ", n)
	out := make([]string, len(lines))
	for i, line := range lines {
		out[i] = pad + line
	}
	return out
}

// splitLines splits a rendered block into lines, treating the empty string
// as no lines rather than one blank line.
func splitLines(s string) []string {
	if s == "" {
		return nil
	}
	return strings.Split(s, "\n")
}
