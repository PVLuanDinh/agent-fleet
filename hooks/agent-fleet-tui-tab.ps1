# PostToolUse (registered on matcher Skill only: a Bash|PowerShell matcher would add a PowerShell start to
# every shell call in every session; the shell branch below stays for a wider matcher): the first time a Claude Code DESKTOP session uses agent-fleet, tell
# the model to open the agent-fleet TUI in a Terminal-panel tab (attached to the shared hidden TUI that
# agent-fleet-ui-keeper.ps1 keeps running). A hook cannot open a Desktop terminal tab itself; the model's
# run_in_terminal tool can, so this injects the instruction once per session.
try {
    if ($env:AGENT_FLEET_TAB_OFF -eq '1' -or $env:CLAUDE_CODE_ENTRYPOINT -ne 'claude-desktop') { exit 0 }
    $in = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $uses = $false
    if ($in.tool_name -eq 'Skill') {
        $uses = "$($in.tool_input.skill)" -match '^agent-fleet$'
    } elseif ($in.tool_name -in 'Bash', 'PowerShell') {
        $uses = "$($in.tool_input.command)" -match 'agent-(fleet|board)[\\/]+scripts|fleet\.py'
    }
    if (-not $uses) { exit 0 }
    $sid = if ($in.session_id) { $in.session_id } else { $env:CLAUDE_CODE_SESSION_ID }
    $marker = Join-Path $env:TEMP "agent-fleet-tui-tab-$sid"
    if (Test-Path $marker) { exit 0 }
    New-Item -ItemType File -Path $marker -Force | Out-Null
    $fleet = Join-Path $env:USERPROFILE '.claude\skills\agent-fleet\scripts\fleet.py'
    # Single-quoted pieces only: in a double-quoted PowerShell string a backtick is an escape (`a became a bell).
    $ctx = 'agent-fleet is in use in this Claude Code Desktop session (every Desktop session that uses agent-fleet ' +
        'starts the TUI terminal session). Do this ONCE now, before continuing: ' +
        '(1) run: python "' + $fleet + '" ui start   (ensures the shared hidden TUI runs); ' +
        '(2) call mcp__terminal__run_in_terminal with command: wsl.exe -d Ubuntu --exec tmux -L fleetui attach -t ui ' +
        '  and title: agent-fleet TUI   (load it with ToolSearch select:mcp__terminal__run_in_terminal if deferred); ' +
        '(3) tell the user the tab is open and to leave it with ctrl+b d, never q (q quits the shared TUI). ' +
        'Skip only if a tab attached to tmux -L fleetui is already open in this session (mcp__terminal__list_terminal_tabs).'
    @{ hookSpecificOutput = @{ hookEventName = 'PostToolUse'; additionalContext = $ctx } } | ConvertTo-Json -Compress -Depth 4
} catch { }
exit 0
