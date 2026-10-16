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

## WSL lanes: run these (and any other Windows hook) inside agent-fleet lanes

agent-fleet lanes are WSL `claude` processes. They read WSL's own `~/.claude`, so Windows hooks, plugins and skills do
not reach them by themselves.

- Skills: `ln -s /mnt/c/Users/<you>/.claude/skills ~/.claude/skills` in WSL (move any existing folder aside first).
- Plugins: `claude plugin marketplace add <owner/repo>` + `claude plugin install <name>@<marketplace>` in WSL.
- Hooks: `win_hook.py` runs the WINDOWS hook from WSL through interop, translating Linux paths in the hook input to
  Windows paths and Windows paths in its output back, and keeping the exit code (a blocking gate still blocks). One
  source of truth: edit the Windows hook and lanes get it. WSL `~/.claude/settings.json` entry, for example:

```json
"PreToolUse": [
  { "matcher": "Write|Edit|Bash", "hooks": [ { "type": "command", "timeout": 15, "command": "python3",
    "args": ["/mnt/c/Users/<you>/.claude/hooks/win_hook.py", "ps1", "C:\\Users\\<you>\\.claude\\hooks\\your-gate.ps1"] } ] }
]
```

  `win_hook.py claude-mem hook claude-code <event>` runs the Windows claude-mem plugin's own runner, so lanes report to
  the worker on Windows localhost even though WSL (default NAT networking) cannot reach that port itself; disable the
  WSL copy of the claude-mem plugin then. Node-only hooks can run on WSL's node directly.
- Trap: Claude Code refuses Write/Edit under any `.claude` folder as a sensitive file, and allow rules do not
  override it, so lanes write logs and observation files there with Bash (`cat > f <<'EOF'`, `printf ... >>`), append only.
