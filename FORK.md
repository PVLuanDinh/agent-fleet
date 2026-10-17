# agent-fleet-tui

A fork of [agent-manager](https://github.com/YoanWai/agent-manager) v0.41.0 by YoanWai,
licensed under Apache-2.0 (see `LICENSE` and `NOTICE`, kept unchanged). It is the terminal UI of
[agent-fleet](https://github.com/PVLuanDinh/agent-fleet). The binary is `agent-fleet`; keys and actions match
upstream, and every upstream MCP tool is reachable as a parity-tested CLI subcommand.

## Changes from upstream (Apache-2.0 section 4(b))

Modified files, all under `internal/ui/`, plus `DESIGN.md` and `.impeccable/design.json`:

- `theme.go`: new default themes `fleet` and `fleet light` (native desktop system palette);
  auto-theme fallbacks point at them. Bundled upstream themes are unchanged and selectable.
- `banner.go`: wordmark reads `agent-fleet`, plain bold lowercase with an accent sweep.
- `surface.go`, `view.go`: hairline frame instead of the sextant/half-block frame; accent-tinted
  selection band.
- `listview.go`: session rows carry state, tool and age in fixed columns chosen once per rail, so
  states align; header gains a board strip.
- `boardstrip.go` (new): reads an agent-fleet message board named by `$AGENT_FLEET_BOARD` and shows
  open owner questions and claims in the header. Without the variable nothing runs.
- `styles.go`: legend titles and keys by weight instead of filled chips; resource gauges no longer
  use the error color; FOCUS badge is color-only.
- `modals.go`, `confirm.go`, `help.go`, `notices.go`: card titles without decorative glyphs; product
  name strings read agent-fleet.
- Tests in `internal/ui` updated to the new look.

- `ownbuild.go` (new), `model.go`, `notices.go`: this build owns its releases. It never polls upstream's
  release list or announcement feed, so it never offers to self-update into stock agent-manager, and the
  messages panel never opens by itself (it opens on the messages key). Upstream's flow stays in the code
  behind the `ownBuild` flag and its tests run with the flag off.

- Binary, CLI and MCP server renamed to `agent-fleet` (command vocabulary, launch notes, help and usage,
  tmux footer, notifications, issue reports). The config directory keeps upstream's name so existing
  sessions carry over; the `AGENT_MANAGER_SESSION_ID` variable is unchanged.
- `internal/mcpreg`: `CLIOnly` (default on) registers no MCP server into sessions; they are told to run
  `agent-fleet help`. `internal/report`: issues are filed against PVLuanDinh/agent-fleet.

Build: `go build -ldflags "-X main.version=0.41.0" -o agent-fleet .`
There is no in-app update: new versions of this build come from this branch.
