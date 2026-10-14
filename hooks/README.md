# Optional Claude Code hooks (Windows + WSL)

Both are PowerShell, fail open, and print nothing unless they have something to add.

| Hook | Event | Does | Off |
|---|---|---|---|
| `agent-fleet-ui-keeper.ps1` | SessionStart | keeps ONE hidden agent-manager TUI running in WSL (`tmux -L fleetui`, session `ui`) so session status stays current; detached, idempotent | `AGENT_FLEET_UI_OFF=1` |
| `memtrim-guard.ps1` | SessionStart | a memory file over budget gets a snapshot and the instruction to move (never delete) its text into a DETAIL file, proven by `scripts/memtrim.py verify`; warns when `MEMORY.md` passes 90% of its cap; needs `~/.claude/memtrim.json` (SKILL.md section 8) | `MEMTRIM_OFF=1` |
| `agent-fleet-tui-tab.ps1` | PostToolUse, matcher `Skill` | in Claude Code Desktop, the first `Skill(agent-fleet)` of a session tells Claude to open that TUI in a Terminal-panel tab | `AGENT_FLEET_TAB_OFF=1` |

Copy the ones you want into `~/.claude/hooks/`, then add to `~/.claude/settings.json` (merge into existing arrays):

```json
"SessionStart": [
  { "matcher": "startup|resume|clear|compact", "hooks": [ { "type": "command", "timeout": 10,
    "command": "powershell -NoProfile -ExecutionPolicy Bypass -File \"%USERPROFILE%\\.claude\\hooks\\agent-fleet-ui-keeper.ps1\"" } ] },
  { "matcher": "startup|resume|clear|compact", "hooks": [ { "type": "command", "timeout": 20,
    "command": "powershell -NoProfile -ExecutionPolicy Bypass -File \"%USERPROFILE%\\.claude\\hooks\\memtrim-guard.ps1\"" } ] }
],
"PostToolUse": [
  { "matcher": "Skill", "hooks": [ { "type": "command", "timeout": 10,
    "command": "powershell -NoProfile -ExecutionPolicy Bypass -File \"%USERPROFILE%\\.claude\\hooks\\agent-fleet-tui-tab.ps1\"" } ] }
]
```

Use your real home path in place of `%USERPROFILE%` if your shell does not expand it. They assume the WSL
distro `Ubuntu`; the skill itself reads `AGENT_FLEET_WSL_DISTRO`. Keep the PostToolUse matcher to `Skill`:
matching Bash/PowerShell would start PowerShell on every shell call.
