# SessionStart: keep ONE hidden agent-fleet TUI running in WSL (tmux socket fleetui, session ui).
# only while its TUI runs; without it a parked or killed session keeps reading "starting".
# Detached and idempotent: `fleet.py ui start` returns at once when the TUI already runs, so many
# sessions starting together cost one TUI. Never blocks the session. Watch it live:
#   wsl.exe -d Ubuntu --exec tmux -L fleetui attach -t ui
# Off: $env:AGENT_FLEET_UI_OFF=1. Fails open: any error -> silent exit 0.
# Optional office: set $env:AGENT_OFFICE_ENSURE to a WSL command that starts an agent office web UI only when it
# is down (e.g. '~/office/restart.sh --ensure'); this hook then runs it detached. Unset = no office, TUI only.
# Off on its own: $env:AGENT_OFFICE_OFF=1.
try {
    if ($env:AGENT_OFFICE_ENSURE -and $env:AGENT_OFFICE_OFF -ne '1' -and (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        # One quoted argument: Start-Process joins the list with bare spaces, which would hand the command's
        # arguments to bash as $0.
        Start-Process -FilePath wsl.exe -ArgumentList @('-d', 'Ubuntu', '--exec', 'bash', '-lc', "`"$($env:AGENT_OFFICE_ENSURE)`"") -WindowStyle Hidden
    }
    if ($env:AGENT_FLEET_UI_OFF -eq '1') { exit 0 }
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { exit 0 }
    $fleet = Join-Path $env:USERPROFILE '.claude\skills\agent-fleet\scripts\fleet.py'
    if (-not (Test-Path $fleet)) { exit 0 }
    $py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    if (-not (Test-Path $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
    Start-Process -FilePath $py -ArgumentList @('-X', 'utf8', '-B', "`"$fleet`"", 'ui', 'start') -WindowStyle Hidden
} catch { }
exit 0
