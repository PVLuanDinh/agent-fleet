---
name: agent-fleet
description: A native-desktop operator workspace for many AI coding agents, in the terminal.
colors:
  backdrop: "#1c1c1e"
  surface: "#3a3a3c"
  overlay: "#48484a"
  border: "#38383a"
  bright: "#f5f5f7"
  text: "#d1d1d6"
  dim: "#aeaeb2"
  subtle: "#949499"
  accent: "#0a84ff"
  accent-secondary: "#64d2ff"
  working: "#ff9f0a"
  waiting: "#bf5af2"
  finished: "#30d158"
  errored: "#ff453a"
  idle: "#949499"
  rail-fill: "#2d2d2f"
  block-fill: "#272729"
  hairline: "#444446"
  selection-band: "#304963"
  lifted-band: "#245b94"
  drop-band: "#476877"
  search-band: "#263e59"
  card-frame: "#28537f"
  lead-fill: "#17395d"
typography:
  title:
    fontFamily: "terminal monospace"
    fontWeight: 700
    lineHeight: 1
  body:
    fontFamily: "terminal monospace"
    fontWeight: 400
    lineHeight: 1
  label:
    fontFamily: "terminal monospace"
    fontWeight: 400
    lineHeight: 1
  key:
    fontFamily: "terminal monospace"
    fontWeight: 700
    lineHeight: 1
spacing:
  cell: "1ch"
  gutter: "2ch"
  legend-gap: "2ch"
  card-pad: "3ch"
  legend-title-column: "11ch"
components:
  rail:
    backgroundColor: "{colors.rail-fill}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
  session-row-selected:
    backgroundColor: "{colors.selection-band}"
    textColor: "{colors.bright}"
    typography: "{typography.title}"
  session-row-lifted:
    backgroundColor: "{colors.lifted-band}"
    textColor: "{colors.bright}"
  session-row-drop-target:
    backgroundColor: "{colors.drop-band}"
  search-field:
    backgroundColor: "{colors.search-band}"
    textColor: "{colors.bright}"
  modal-card:
    backgroundColor: "{colors.block-fill}"
    textColor: "{colors.text}"
    padding: "0 3ch"
  keycap:
    textColor: "{colors.bright}"
    typography: "{typography.key}"
  legend-lead:
    backgroundColor: "{colors.lead-fill}"
    textColor: "{colors.accent}"
    typography: "{typography.key}"
  legend-tier-title:
    textColor: "{colors.bright}"
    typography: "{typography.title}"
  wordmark:
    textColor: "{colors.bright}"
    typography: "{typography.title}"
---

# Design System: agent-fleet

## Overview

**Creative North Star: "The Native Departures Board"**

agent-fleet is a fork of agent-manager v0.41.0 that refuses the generic tmux-tool look. The default world reads like a sidebar-and-content window of a native desktop app: graphite surfaces, the system state colors, a four-step label hierarchy, a source-list selection band, and hairline separators instead of drawn boxes. Inside that window the session rail behaves like a split-flap departures board: columns never move, and a state change restyles its own cell.

It stays a dense instrument used for hours. Structure comes from fixed columns, consistent 2-column insets and tonal fills; color is spent on state and focus, and the frame stays quiet until an agent needs you. Every color resolves through the active `Theme`, so the bundled themes (classic, solarized, catppuccin, tokyo night, gruvbox, nord, dracula, rosé pine, monochrome, kanagawa, paper and their light sides) stay selectable on the same structure. `fleet` is the default and leads the picker; `fleet light` is its counterpart, and auto-detect falls back to `fleet` / `fleet light` when a stored theme has no counterpart.

**Key Characteristics:**
- Native split view: rail fill, a single `│` hairline separator, full-width hairline rows above and below the body.
- Split-flap rail: state, tool and age in fixed right-aligned columns that never shift when a row changes.
- System palette: graphite neutrals, one blue accent, the OS state colors, red reserved for failure.
- Weight over chrome: keys, titles and the wordmark are bold Bright text, never filled buttons.
- Terminal-native: one monospace face, one row per header, keyboard first.

## Colors

A graphite native-desktop palette with one blue accent and the system state colors; every non-anchor tone is derived by mixing two anchors, so a theme only declares its anchors.

### Primary
- **System Blue** (accent): focus, the narrow-width wordmark, the update tag, card titles, inbox and after-turn badges, the focused pane's edge, and the tint inside every selection band.

### Secondary
- **Sky Cyan** (accent-secondary): groups and scopes: group names, the scope badge, the drop-target band, the divider grip while dragging.

### Neutral
- **Graphite Backdrop** (backdrop): the window. Views leave it unpainted and the terminal's own background is synced to it (OSC 10/11), so window padding and cells match.
- **Raised Graphite** (surface): chip fill and the base of the selection band.
- **Gauge Track Gray** (overlay): unfilled gauge track.
- **Seam Gray** (border): idle borders and the divider's hairline.
- **Title White** (bright): selected names, keys, legend tier titles, the wordmark, values that must win.
- **Body Silver** (text), **Secondary Gray** (dim), **Tertiary Gray** (subtle): the remaining three steps of the label hierarchy; subtle also carries separators and labels.
- **Rail Graphite** (rail-fill): the sessions rail, mix(backdrop, surface, .55).
- **Block Graphite** (block-fill): modal cards and section blocks, mix(backdrop, surface, .35), quieter than the rail.
- **Hairline** (hairline): every frame rule and the rail separator, mix(backdrop, text, .22): a seam, never a border.

### State
- **Working Orange**, **Waiting Purple**, **Finished Green**, **Errored Red**, **Idle Gray**: agent state, always paired with a glyph (`◐` working, `◌` starting, `◆` waiting, `●` finished, `✕` errored/dead, `○` idle) and, on session rows, the state word.

### Named Rules
**The Source-List Rule.** The selection band is surface tinted 20% toward the accent (selection-band), never a plain gray; a row lifted for reordering goes to 45% (lifted-band); a drop target tints 30% toward accent-secondary (drop-band).

**The Red Is Failure Rule.** Errored red appears only for an agent that errored or died. Resource gauges ramp finished → working and stop at working at 85% and above, so a scan for red finds failures only.

**The Waiting Means You Rule.** Open owner questions on the board strip take the waiting color and the waiting glyph, because they wait on you exactly as a waiting agent does.

## Typography

**Display Font:** the user's terminal monospace (no fallback is chosen by the app)
**Body Font:** the same face
**Label/Mono Font:** the same face

**Character:** One face at one size. Hierarchy comes from weight (bold vs regular) and the four label steps, never from tracking, case or size.

### Hierarchy
- **Title** (700, one cell): the wordmark, selected session names, legend tier titles, card titles (accent), the "agents total usage:" heading.
- **Body** (400, one cell): session names (text), prompts, replies, values.
- **Label** (400, one cell): field labels, meta columns on unselected rows, separators (subtle); the dim step for secondary copy and quiet-tier labels.
- **Key** (700, one cell): every key binding, set in bright beside its action in text.

### Named Rules
**The Plain Wordmark Rule.** The wordmark is `agent-fleet`, lowercase, bold, in bright, one row: a window title, not a splash. An accent-tinted highlight sweeps it left to right on launch (22 frames at 110 ms) and again every 30 s, then holds still so the header can be selected with the mouse. A terminal too narrow for the lit wordmark shows the name in the accent alone.

**The Weight Not Buttons Rule.** A key is set apart from its action by weight, the way a native menu shows a shortcut, not by a drawn keycap.

## Layout

- **Frame:** one header row, a full-width hairline row on the backdrop, the body, a closing full-width hairline row, then the legend footer (at most 3 rows; the tail is marked `…` and the full map is one `?` away).
- **Split:** the rail takes 30% of the width by default (minimum side 30 columns), resizable by drag or keys; the divider takes the accent while resized and accent-secondary while dragged.
- **Rail:** filled with rail-fill from the row under the top hairline to the row above the closing one; its right edge is a single `│` hairline on the backdrop. In focus mode that column lights in the accent beside the focused pane, with `╭` and `╰` corners.
- **Insets:** every rail and content line shares a 2-column gutter; the legend aligns tier bindings on an 11-column title column with 2-column gaps.
- **Header:** wordmark left; right side, in order: agent scope count, fleet state counts, the board strip, agent usage. As width shrinks the header sheds in this order: state words (counts keep glyph + number), board claims, agent usage, the board strip, then everything but the scope.

### Named Rules
**The Split-Flap Rule.** Session rows set state (8 columns), tool (8) and age (4, right-aligned bare age such as `12m`) in fixed columns right-aligned to the rail. The rail picks ONE meta level for every row from the widest visible row head (full, state + age, state only), so states start in one column down the list. A state change restyles its cell; it never shifts the grid.

**The Badges Never Move Rows Rule.** Inbox and after-turn badges are left out of the meta-level calculation; a badged row that no longer fits sheds its own meta. Name and inbox badge outrank meta: a tight row shortens the name before it drops a badge.

## Elevation & Depth

Flat. There are no shadows. Depth is tonal: backdrop, block-fill, rail-fill, selection band, lifted band. The frame is two hairlines and a vertical separator, never a drawn box around the body. A modal card earns its raised role through a complete rounded frame in card-frame (border tinted 35% toward the accent) and centered placement on the backdrop.

### Named Rules
**The Two Quiet Lines Rule.** The body is framed by a hairline above and below and the rail's `│` separator; nothing else draws a border around a working surface.

## Shapes

Cell geometry only. Working surfaces are square fills on the cell grid. Modal cards are the one rounded form: `╭ ╮ ╰ ╯` box corners, the title set into the top edge, keys on a foot below a `├─┤` hairline. Gauges are slim heavy rules (`━` with a `╸` half cap). Status glyphs come from one geometric family so a column of them reads as one scale.

## Components

### Session rail
- **Rows:** glyph in the state color, handle, name (text; bold bright when selected), optional prompt or last reply between name and meta, then the fixed meta columns. Meta is subtle, text when selected; the state word always takes its state color.
- **Selection:** a full-width selection band; the rail's edge cell matches the band's tone.
- **Groups:** name in accent-secondary, subtree rollup as state glyphs and counts rather than words.
- **Machine block:** at the rail's foot, a `computer` label and one 4–10 column gauge per resource (cpu, mem, swap, disk, battery), with temperature and network readings.

### Header board strip
- **Source:** reads `$AGENT_FLEET_BOARD` (`msgs/<seq>.json`, `claims/*.claim`) off the update path every 15 s, parsing only messages past the last one seen; absent when the variable is unset.
- **Style:** a subtle `board` label; open questions as waiting glyph + count in bold waiting with the word in waiting; claims quiet (count in text, label in subtle); "no questions" in subtle. The compact form shows questions only and disappears when there are none.

### Legend footer
- **Tier titles:** plain bold bright (quiet tiers in bold subtle), no filled badge.
- **Bindings:** key in bold bright, foreground only so nothing punches through a band; action in text (dim for a quiet tier, joined by ` · `).
- **Lead binding:** the tier's primary action (quick prompt, review mode) sits on a lead-fill chip with the key in the accent and the label in bright.

### Divider
- A quiet bold label (subtle) followed by a hairline in border out to the edge, the way a native inspector groups fields.

### Modal card
- **Frame:** rounded, card-frame tone, block-fill body, 3-column inner padding, title in bold accent set into the top edge.
- **Foot:** an untitled run of keycaps under a hairline.

## Do's and Don'ts

### Do:
- **Do** resolve every color through the active theme; derive in-between tones with `mix` from the anchors.
- **Do** keep session meta in the fixed 8 / 8 / 4 columns and pick one meta level for the whole rail.
- **Do** pair every state color with its glyph, and the state word on session rows.
- **Do** set keys in bold bright, foreground only, on any row that can carry a band.
- **Do** frame working surfaces with hairlines (hairline tone) and the single `│` separator.
- **Do** reserve errored red for failed agents and stop gauges at the working tone.

### Don't:
- **Don't** shift columns when a row's state, badges or name change.
- **Don't** draw boxes around the rail or content; only modal cards are framed.
- **Don't** set the wordmark in tracked capitals, gradients or more than one row.
- **Don't** fill a mark (badge, keycap) that sits on a selection band; a fill punches through it.
- **Don't** animate without state meaning; the wordmark sweep is the one ambient motion and it rests between sweeps.

### Known limits
- At about 72 columns, depth-2 rows clip the state word; the glyph still carries the state.
- At the default 30% rail the age column sheds whenever the longest name leaves no room for it.
