# SessionStart: memory files over budget -> tell Claude to trim them losslessly (agent-fleet scripts/memtrim.py).
# Config: ~/.claude/memtrim.json (see SKILL.md section 8). Off: $env:MEMTRIM_OFF=1. Fails open.
try {
    if ($env:MEMTRIM_OFF -eq '1') { exit 0 }
    $m = Join-Path $env:USERPROFILE '.claude\skills\agent-fleet\scripts\memtrim.py'
    if (-not (Test-Path $m)) { exit 0 }
    $py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    if (-not (Test-Path $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
    $in = [Console]::In.ReadToEnd()
    $in | & $py -X utf8 -B $m hook
} catch { }
exit 0
