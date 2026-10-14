# agent-fleet (formerly agent-board)

Stdlib-only Python 3.10+ Claude Code skill: file-based, hash-chained message board for agents across processes, an anti-gaming scorer, and a bridge to agent-manager (tmux agent fleet, in WSL on Windows). No dependencies, no config; agent-manager is optional.

## Commands

```bash
python scripts/selfcheck.py     # must end with a line starting "ALL GREEN"; on Windows also run it under WSL
./setup.sh [--force]            # copy into ~/.claude/skills/agent-fleet, then self-check
```

## Architecture

```
SKILL.md                skill definition and full command reference (loaded by Claude Code)
scripts/board.py        append-only hash-chained messages, claims, owner questions, verify, head
scripts/view.py         read-only lean reads: digest, brief (per-agent cursor), show
scripts/review.py       blind peer review: submit, next, post, reply, resolve, open, check
scripts/score.py        open/score a task: re-runs the verify command, 4 dimensions multiplied
scripts/fleet.py        agent-manager bridge: run it via WSL, read its live leases, MCP config, fleet on/off
scripts/selfcheck.py    reachable-red cases for every cheat class, board mechanics, concurrency
references/             scorer-research.md, efficiency-research.md
```

## Rules

- `score.py open` pins the self-hash of `score.py` and `board.py`. Any change to either invalidates every open task's integrity. That is why `view.py` and `review.py` are separate scripts: do not fold new features into the pinned two without re-opening tasks.
- Every new guard or gate needs a reachable red case in `selfcheck.py`.
- One claim system: board claims. With `fleet.py --board X on`, `board.py claim` also refuses paths an agent-manager session leases exclusively, and fails closed when the leases cannot be read. Never add a second claim store.
- Stdlib only. Do not edit `SKILL.md` command tables without checking the commands exist.
- The scorer is detection, not prevention (same-OS-user agents); keep docs honest about it.

See [CONTRIBUTING.md](CONTRIBUTING.md).
