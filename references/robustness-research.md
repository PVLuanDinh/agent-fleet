# Robustness: what comparable fleets do, and agent-fleet's gaps (webgrab research, 2026-10-29)

Question: "is agent-fleet bullet-proof?" Answer: **no**. Sources were read with webgrab
(GitHub READMEs and one design doc). Each line paraphrases its source; nothing here is a quote.

## Sources

| Repo | What it is | Robustness feature worth having |
|---|---|---|
| dicklesworthstone/mcp_agent_mail (Python, now superseded by a Rust rewrite) | Mail + advisory file leases for coding agents, Git + SQLite backed | Pre-commit guard (already borrowed: `claim_guard.py`); `ack_required` messages; a **doctor** that finds stale lock files left by crashed processes and expired leases, and applies only the safe repairs automatically |
| beadhub/bdh (Go) | Coordination CLI over Beads | **Automatic, short leases**: files you modify are reserved for 5 minutes and renewed while you work; a **PostToolUse hook surfaces pending messages after every tool call** |
| nikiforovall/tmux-message-bus | SQLite-WAL mailbox for agents in tmux | Identity derived from the session (survives renames and `--resume`); durable, ordered, no-duplicate delivery; Stop hook drains the inbox; a **sweep marks crashed agents dead and requeues their orphaned claims** |
| mkalkere/agent-coordinator | FastAPI + SQLite-WAL coordination server | Atomic task claim; lease file locks that auto-expire after a crash; **stale agent at 30 min, resources reclaimed at 60 min**; message TTL + acks |
| gastownhall/beads (`bd`) | Graph issue tracker for agents (Dolt) | `bd ready` (unblocked work) + atomic `--claim`; hash IDs that never collide across branches; audit trail per item |
| raine/workmux | git worktrees + tmux windows per agent | **`resurrect`** restores agent windows after a tmux or computer crash from persisted state; **`reap-agents`** exits tracked agents older than a threshold; status via lifecycle hooks |
| aws-samples/sample-autonomous-cloud-coding-agents (ORCHESTRATOR.md) | Durable orchestrator for cloud coding agents | **Admission control**: atomic per-user and system concurrency counters; over the limit a task is QUEUED, not failed; heartbeat-loss means crashed; idempotency keys |
| smtg-ai/claude-squad, epilande/ccmux, MustaphaSteph/agent-bus | tmux/worktree managers, a local message bus | Nothing beyond the above for robustness (UI and convenience) |

## agent-fleet today (territory, read 2026-10-29)

- Board writes are atomic per message: temp file + `os.link` (fails if another writer took the seq); claims use
  `O_EXCL`; lease rewrites use `os.replace`. A corrupt message file is loaded as `_corrupt` and `verify` reds it.
- **No `fsync`** before the link: a power loss or BSOD can leave a truncated message (detected, not prevented).
- Leases exist (`--ttl`, default 4h; `renew` is manual; pre-lease claims never expire). Stale claims are freed by
  `janitor.py clean` only under a recorded owner answer (19 stale claims needed one on 2026-10-29).
- Messages are pull-only: lanes must run `view.py brief` every 20 minutes, the habit SKILL.md says lapses first.
- Guards inside a WSL lane exist only if `win_hook.py` bridges them: on 2026-10-29 a lane ran a forbidden CLI
  because the guard against it was not bridged into WSL yet.
- No reboot recovery: the 2026-10-28 reboot needed a hand-written savepoint index.
- Lane caps (3 builders while Available >= 6 GB) and the heavy-run mutex were prose rules and lane
  scripts, not enforced by `fleet.py spawn`.
- `board.py` and `score.py` still lack the stdio crash guard: their hashes are pinned by open scored tasks.
- The skill folder is not a git repo: no history, no revert; the published github.com/PVLuanDinh/agent-fleet lacks
  the 2026-10-29 crash-guard fixes.
- `score.py` detects tampering, it cannot prevent it (agents run as the same OS user). Documented in SKILL.md.

## Improvements, ranked (real incident first, then cost; P = touches a hash-pinned file, land only when no scored task is open)

1. **Spawn preflight + admission control in `fleet.py spawn`** (AWS admission control). Refuse a spawn whose WSL
   settings lack the required bridged guards (no-codex-guard, lane_claude_guard, hub gate); queue (do not fail) a
   spawn past the lane cap or below the RAM floor. Incident: a lane ran a forbidden CLI; memory-pressure stops. ~60 lines.
2. **Heartbeat-renewed short leases** (bdh, agent-coordinator, tmux-message-bus sweep). A lane-side PostToolUse
   hook renews the lane's own claims with a short TTL (30m); a dead lane's claims expire in minutes and the next
   claimer takes them over on the record, with no owner answer needed. Uses the existing `renew`. Incident: 19 stale claims.
3. **Push delivery** (bdh, tmux-message-bus). A lane PostToolUse hook prints "N new board messages to you" when the
   head passed the lane's cursor (peek, never advance). Replaces the 20-minute habit that lapses.
4. **`doctor`** (mcp_agent_mail). One report: `verify`, stale claims, stale lock files (review locks,
   `HEAVY-RUN.lock`), BLOCKED sessions, unbridged lane hooks, digest budget, open challenge count. Applies only the
   safe repairs (expired leases); everything else stays an owner question. Incident: HEAVY-RUN.lock removed by hand; 78 open challenges.
5. **Read receipts** (mcp_agent_mail `ack_required`). `view.py seen N`: which addressees' cursors have not passed
   message N. Per-agent cursors already exist; a few lines.
6. **`fleet.py resurrect`** (workmux). Persist a spawn manifest (name, dir, tool, brief path); after a reboot,
   respawn every lane that was live. Incident: the 2026-10-28 reboot savepoint.
7. **Version control.** `git init` the skill folder locally now (history + revert); push the fixes to the published
   repo on the owner's word.
8. **`fsync` before link** in `board.post` and claim writes (P). Durability against power loss.
9. **stdio guard in `board.py` / `score.py`** (P). Owed since the 2026-10-29 crash rebuild.
10. **Atomic `take N` on request messages** (beads `--claim`, agent-coordinator). Already on the efficiency to-do list.

## Considered and rejected

- **Move the board to SQLite WAL** (tmux-message-bus, agent-coordinator, mcp_agent_mail). Gains atomic multi-row
  claims and queries. Rejected: it breaks the per-file hash chain, `score.py` anchors and `--min-head` pinning,
  which are the anti-gaming core; per-message writes are already atomic. Revisit only if the board outgrows files.
- **Session-derived identity** (tmux-message-bus). Stops a lane posting under another name; needs `board.py` to
  stamp the fleet session id (P). Low priority while every lane is spawned by the integrator.

## Status (2026-10-29: all 10 built)

| # | Fix | Where | State |
|---|---|---|---|
| 1 | spawn preflight + admission | `fleet.py admission`, rows `no-codex-guard-bridge` + `lanehook`, project `max_active_lanes` / `min_available_mb` | LIVE |
| 2 | self-renewing short leases | `lanehook.py` (renew to 30m inside the last 15m) | LIVE (WSL PostToolUse) |
| 3 | push mail alerts | `lanehook.py` (peek, once per new message) | LIVE |
| 4 | doctor | `doctor.py` (+ project `locks`) | LIVE |
| 5 | read receipts | `view.py seen N` | LIVE |
| 6 | resurrect | `fleet.py resurrect` + roster + heartbeat (SessionEnd marks a clean end) | LIVE for lanes spawned from now on |
| 7 | version control | local git, `* -text` so pinned hashes never change on checkout | LIVE |
| 8 | fsync before link/replace | `board.py write_durable`, `score.py` spec write | LIVE (landed 2026-10-29 in a window with no task open) |
| 9 | stdio guard in board.py / score.py | both `main()` | LIVE |
| 10 | atomic take of requests | `review.py requests` / `take N` | LIVE |

Each fix has selfcheck cases with a red shown to fire: before the code (1-5), or by mutating a temp copy (6, 10,
the heartbeat, 8-9). selfcheck: ALL GREEN on Windows and WSL, durability cases included, after the landing.

## Independent review (2026-10-29), folded

An independent reviewer read the whole change: 2 BLOCK, 5 MAJOR, 6 MINOR, and 4 tests that could not fail. Folded the same day:
lanehook through fastboard's cache with the throttle saved first (an uncached renew took 53 s, past the 20 s hook
timeout); lane identity by agent-fleet session id, silent when ambiguous; leases never shortened; Stop recorded so a
finished lane is not resurrected; the roster as one file per lane (no lost update), recorded only after a successful
spawn; kill forgets each lane it actually killed; an admission lock from check to spawn; unknown statuses count as
active; the resume note never stacks; doctor matches boards by samefile; brief's cursor from its first read; broadcast
receipts list agents only; every file LF. Each fix has a case that a mutation of it turns red.

## Round 2: the limits that remain (webgrab research, 2026-10-29)

Sources: Claude Code's sandboxing docs (code.claude.com/docs/en/sandboxing); a 2026 write-up of four Claude Code
sandbox and worktree escape fixes (versions 2.1.216-2.1.219); webcoyote/awesome-AI-sandbox; Kleppmann, "How to do
distributed locking" (fencing tokens); the Batty tmux agent supervisor (dev.to); a reverse-engineering of Claude Code
Agent Teams' task claims (claim expiry). Paraphrased, not quoted.

| Remaining limit | What the field does | Fit here |
|---|---|---|
| Agents can edit the board, the scorer, any file (same OS user) | Claude Code's own sandbox (bubblewrap on Linux/WSL2): `sandbox.filesystem.denyWrite`, permission `deny` rules for Edit/Write, `allowUnsandboxedCommands: false`. Others: a separate OS user per agent, Landlock, containers | PARTIAL. WSL lanes can be sandboxed, but a Windows binary launched from WSL (anything under /mnt/c, e.g. a Windows venv python.exe) runs on the Windows host, outside the sandbox. Blocking that socket breaks lanes that test with Windows Python; allowing it leaves a write path. Real prevention for such a fleet is still a scorer run as another Windows user or in CI. Checked: WSL Claude Code 2.1.312 and Windows 2.1.295, both past the escape fixes; bwrap present in WSL, socat (needed for the network proxy) not installed; Ubuntu 26.04 needs the AppArmor user-namespace allowance |
| A lease lapses during one long tool call | Liveness from the process, not from activity: a supervisor renews while the holder is alive (Batty polls panes; distributed leases renew on heartbeat) | GOOD. Renew each roster lane's claims from outside while its agent-fleet session is alive: a small loop in the hidden TUI session (`fleet.py renew-live`, every 5 minutes) |
| A lane writes after its lease lapsed | Fencing tokens: the resource rejects a writer whose token is older than the current one | GOOD, simplified: `claim_guard` refuses a lane's commit of a file whose claim BY THAT LANE has expired (today it only refuses files someone else holds) |
| A `take` never expires | Claim expiry: a task held N minutes without a heartbeat returns to ready (Agent Teams pattern) | GOOD. Give takes a lease; lanehook renews the lane's own takes; an expired take reopens on the record |
| A lane that hits its context limit sits unresponsive | Batty detects the context-exhaustion message and flags the agent for restart | GOOD, tiny: add the pattern to `fleet.py` BLOCKERS so `sessions` and `doctor` report it |
| Names are self-declared | Ed25519 key per agent, signed messages (alien-id/agent-id, A2A discussions) | NOT YET: with one OS user every agent can read every key, so signatures prove nothing. Revisit only with per-lane OS isolation |
| Restart after a crash is manual (`resurrect`) | Supervisors restart dead members automatically, with backoff | KEEP MANUAL: auto-restart can loop on a lane that crashes on start. At most, list cut-off lanes at session start |

Ranked by value per cost: renew-live (1), stale-writer fencing in claim_guard (2), take leases (3), the context
pattern (4), the sandbox for WSL lanes without Windows-binary access (5, partial).

### Round 2 built (2026-10-29)

| # | Fix | State |
|---|---|---|
| 1 | `fleet.py renew-live` from session liveness, run by the hidden TUI every 300 s | LIVE (window `renew`; first pass 04:21 renewed 0: the one roster lane held no claims) |
| 2 | claim guard refuses a lane's commit of a file whose own claim lapsed | LIVE |
| 3 | takes are leases (renewed, expire, reopen, takeover on the record) | LIVE |
| 4 | context-limit blocker in `fleet.py sessions` / `doctor` | LIVE |

Built in a separate copy while lanes were spawning, then swapped in file by file; selfcheck ALL GREEN on Windows
(306, 1 SKIP) and WSL (306); every new case was shown to fail with its fix mutated. First live proof of the lane hook:
a reviewer lane spawned after the change wrote heartbeat `tool`, found by its agent-fleet session id.
