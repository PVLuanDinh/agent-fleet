# agent-fleet (terminal UI and CLI)

The fleet layer of [agent-fleet](https://github.com/PVLuanDinh/agent-fleet): runs each coding agent
(Claude Code, Codex, Gemini, OpenCode, ...) as a live tmux session, shows which one is working, waiting
on you, finished or errored, and lets you prompt, review diffs, revive and coordinate them from one
terminal. The `agent-fleet` skill drives it through `fleet.py`; a person drives it through the TUI.

- **CLI only.** No MCP server is registered into sessions. Every operation an agent needs (`sessions`,
  `send`, `read`, `spawn`, `task`, `reserve`, `wait`, `review-comment`, ...) is a subcommand; a spawned
  session is told to run `agent-fleet help`.
- **Self-contained.** No release checks, remote feed or self-update; issue reports go to
  [PVLuanDinh/agent-fleet](https://github.com/PVLuanDinh/agent-fleet/issues).
- **Board in the header.** With `AGENT_FLEET_BOARD` set to an agent-fleet message board, the header shows
  open owner questions and claims.

## Build

Linux or macOS (on Windows: inside WSL), Go 1.27+, tmux 3.2+:

```bash
git clone -b tui https://github.com/PVLuanDinh/agent-fleet.git agent-fleet-tui && cd agent-fleet-tui
go build -ldflags "-X main.version=0.41.0" -o ~/.local/bin/agent-fleet .
agent-fleet            # the TUI
agent-fleet help       # the CLI
```

Settings live in `~/.config/agent-fleet`. Usage, configuration and notifications: [docs/](docs/).
Design system: [DESIGN.md](DESIGN.md).

## License

Apache-2.0. This is a fork of an Apache-2.0 project; its origin, the unchanged `LICENSE` and `NOTICE`,
and every change made here are listed in [FORK.md](FORK.md).
