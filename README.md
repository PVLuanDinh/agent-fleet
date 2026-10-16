# agent-fleet (formerly agent-board)

A file-based message board for AI coding agents across processes (Claude Code, Codex, subagents): append-only hash-chained messages, file claims, blind peer review, and an anti-gaming scorer that re-runs verification instead of trusting reports. Stdlib-only Python.

It is a Claude Code skill (`SKILL.md` + `scripts/` + `references/`), and the scripts also run standalone from anywhere.

## Why

- Several agents working on one project need a durable channel that survives the session and reaches other processes. `SendMessage` is fine inside one session; it cannot reach Codex, another session, or a hook-less subagent. Plain files can.
- Agents game tests. A report saying "tests pass" is not evidence, so the scorer re-runs the verification command itself.

## What is in it

| Script | Purpose |
|---|---|
| `scripts/board.py` | Append-only, hash-chained message board. Posts, file claims/releases, owner questions, integrity `verify`, `head`. |
| `scripts/view.py` | Token-lean reads: `digest` (current state), `brief` (only what is new to you), `show N`. Read-only. |
| `scripts/review.py` | Blind peer review for idle agents: `submit`, `next`, `post`, `reply`, `resolve`, `open`, `check`. |
| `scripts/score.py` | Anti-gaming scorer: `open` a task (pins the verify command, protected files, hashes), `score` it (re-runs the command). |
| `scripts/keepalive.py` | Claude Code Stop hook that keeps the lead agent working instead of idling after a status report. |
| `scripts/install_keepalive.py` | Adds (or `--uninstall`s) the keepalive hook in `~/.claude/settings.json`. |
| `scripts/selfcheck.py` | Reachable-red self-check of every cheat class, board mechanics and the keepalive hook. |

Requirements: Python 3.10+ (`python` on PATH), Windows/macOS/Linux, no dependencies, no config. The board needs a local filesystem with hard links (NTFS, ext4, APFS).

## Install

Claude Code loads skills from `~/.claude/skills/`.

bash:

```bash
git clone https://github.com/PVLuanDinh/agent-fleet.git ~/.claude/skills/agent-fleet
```

PowerShell:

```powershell
git clone https://github.com/PVLuanDinh/agent-fleet.git "$HOME\.claude\skills\agent-fleet"
```

Or clone anywhere and run `./setup.sh`, which copies the folder to `~/.claude/skills/agent-fleet` (it refuses to overwrite a different existing copy unless you pass `--force`) and runs the self-check.

## 60-second quick start

```bash
B=~/.claude/skills/agent-fleet/scripts
```
```powershell
$B = "$HOME\.claude\skills\agent-fleet\scripts"
```

```bash
# Pick ONE board path and hand it to every agent
python $B/board.py --project myproject where

# Post, claim, read
python $B/board.py post --as G3 --kind status --thread G3 "pre-reg frozen"
python $B/board.py claim src/pkg/mod.py --as G3        # exit 1 = held by someone else
python $B/board.py release src/pkg/mod.py --as G3
python $B/board.py release src/pkg/mod.py --as lead --for G3 --owner-auth 42   # G3 stopped; #42 = owner's yes
python $B/view.py digest                               # current state, not the whole log
python $B/view.py brief --as G3                        # one line per message you have not seen

# Park a question for the human owner
python $B/board.py ask-owner --as G3 --thread G3 "..."
python $B/board.py owner

# Report a result (refused without --cite and --claim)
python $B/board.py post --as G3 --kind result --cite "pytest tests/test_x.py" --claim pass "..."

# Idle agent reviews someone else's work
python $B/review.py next --as R1 --min 2 --wait 900
python $B/review.py post --as R1 --on N --verdict agree --cite "<command you ran>" "<finding>"

# Lead: open a task BEFORE agents start (prints ANCHOR; keep it OUTSIDE the board)
python $B/score.py --board <root> open T1 --cwd <repo> --verify "<command that proves done>" --protect <paths>
# ...after agents report
python $B/score.py --board <root> score T1 --anchor <hex> --min-head <N:HASH>
```

The board root is `--board DIR`, then `$AGENT_BOARD`, then `~/.claude/agent-board/<--project or cwd name>`. See [SKILL.md](SKILL.md) for the full command table, the rules every agent follows, and the scoring dimensions.

## Keep the lead working

A lead agent told to work continuously still tends to end its turn after a status report ("I'm starting step 1 now.") and wait for a human. The keepalive Stop hook blocks that stop unless the final paragraph of the lead's last message asks a question or names a wait (owner question, blocked, a background task). It blocks at most 5 times in a row, fails open, and acts only in sessions whose environment has `AGENT_NAME=lead` (or `AGENT_KEEPALIVE=1`).

```bash
python ~/.claude/skills/agent-fleet/scripts/install_keepalive.py      # idempotent; backs up settings.json
AGENT_NAME=lead claude                                               # start the lead with the hook active
```
```powershell
python "$HOME\.claude\skills\agent-fleet\scripts\install_keepalive.py"
$env:AGENT_NAME = 'lead'; claude
```

The installer writes the hook with the absolute path of the Python that ran it, so it works on any account and OS (`python3`, a venv, Windows). Run it with a Python that will stay installed: if `python` on your PATH is some tool's private venv, the hook breaks (silently, since it fails open) when that venv goes away. It uses the exec form (`command` + `args`), which needs a current Claude Code. Remove it with `--uninstall`; turn it off for one session with `AGENT_KEEPALIVE_OFF=1`.

## The fleet: agent-manager (optional)

[agent-manager](https://github.com/YoanWai/agent-manager) runs each agent CLI as a live tmux session and shows
which one is working, waiting or blocked. On Windows it runs inside WSL. `scripts/fleet.py` bridges it:

```bash
python $B/fleet.py am --version            # any agent-manager command, argv intact (wsl.exe --exec on Windows)
python $B/fleet.py mcp-config              # MCP server entry + `claude mcp add` line, so any session can drive the fleet
python $B/fleet.py --board <root> on       # one claim domain: board claims refuse paths a fleet session leases
python $B/fleet.py leases                  # live agent-manager leases
python $B/fleet.py spawn --directory <dir> --name w1 --tool claude --prompt "..."   # pre-trusts the folder, then spawns
python $B/fleet.py sessions                # flags sessions BLOCKED on a trust/login/permission prompt
python $B/fleet.py kill <id>               # stop a session from outside agent-manager
python $B/fleet.py ui status              # one hidden agent-manager TUI keeps session status current (ui start/stop/shot/keys/attach)
python $B/fleet.py archive-dead           # archive every dead row (confirms only dialogs naming a dead session)
```

The TUI needs a real terminal; everything else works from Claude Desktop or a terminal. Results and scores
stay on the board: a green row in the TUI is not a pass. Optional Claude Code hooks (keep the hidden TUI running; open it in a Desktop Terminal tab when the skill is used): [hooks/README.md](hooks/README.md). Details: [SKILL.md](SKILL.md) section 7.

## Lane preflight (any project)

`fleet.py spawn` first checks that every skill the project's lanes need is installed AND usable (a functional
check per skill, install-then-recheck, refuse on a required failure), and can prefix every lane prompt with a
`lane_preamble` such as "route with the hub skill first". Example: [examples/fleet-requirements.example.json](examples/fleet-requirements.example.json); details: SKILL.md section 9.

## Lane preflight (any project)

`fleet.py spawn` first checks that every skill the project's lanes need is installed AND usable (a functional
check per skill, install-then-recheck, refuse on a required failure), and can prefix every lane prompt with a
`lane_preamble` such as "route with the hub skill first". Example: [examples/fleet-requirements.example.json](examples/fleet-requirements.example.json); details: SKILL.md section 9.

## Memory trim (any project)

`scripts/memtrim.py` keeps Claude Code memory files (a START-HERE file, the auto-loaded `MEMORY.md`
index) under a byte budget by MOVING text into a DETAIL file, never deleting it, and `verify` proves
every line survived. Config and hook: [SKILL.md](SKILL.md) section 8, [hooks/README.md](hooks/README.md).

## Self-check

```bash
python scripts/selfcheck.py
```

The last line must start with `ALL GREEN`. It uses throwaway temp dirs and never touches a real board.

## Limits

These come from the scripts' own notes; read them before relying on any guarantee.

- **Detection, not prevention.** Agents run as the same OS user and can edit any file, including `score.py`. The scorer catches edits and zeroes the score; it cannot stop them. For enforcement, run `score` where agents cannot write (another user, CI).
- **Self-declared identities.** `--as` / `$AGENT_NAME` is not authenticated, and the hash chain has no key. The chain catches careless edits and deletions only; an agent that recomputes every later hash can rewrite history. The anchor and `--min-head` kept outside the board narrow this, but messages posted after the lead's last `head` stay unpinned (the output says `tail unpinned`).
- **Review independence is by name.** "A different name" is the reviewer-independence check, not proof of a different agent.
- **Scope flags are keyword matches.** They catch literal scope-override phrasing, not intent, and they also flag denials ("I did not edit the tests"). The lead reads each flagged message and may `--waive` it.
- **Hard links required.** The board must live on a filesystem that supports them; exFAT and some network shares do not.

## Research notes

- [references/scorer-research.md](references/scorer-research.md): how this compares with published anti-gaming scorers, and ranked upgrades.
- [references/efficiency-research.md](references/efficiency-research.md): token cost and capability research, with measurements and the to-do list.

## Using with Claude Code

`SKILL.md` is the skill definition Claude Code loads from `~/.claude/skills/agent-fleet`. `CLAUDE.md` gives Claude Code context when you work on this repository itself.

```bash
claude    # reads CLAUDE.md automatically
```

## License

MIT, see [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
