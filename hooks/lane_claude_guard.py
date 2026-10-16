#!/usr/bin/env python3
"""PreToolUse guard for agent-fleet lanes (WSL claude): DENY direct writes into a .claude folder.

Claude Code treats any .claude path as a sensitive file: a lane's Write/Edit there, or a Bash redirect into it,
turns into a permission prompt, and a live lane PARKS on it unseen. A PreToolUse deny comes before that prompt,
so the lane gets an instant refusal naming the sanctioned path (lane_obs.py) and keeps working.
Reading .claude, and running scripts that live there (lane_obs.py, win_hook.py, board.py), stays allowed.
"""
import json, re, sys
from pathlib import Path

HELPER = str(Path(__file__).resolve().parent / "lane_obs.py")
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
# a redirect/tee/sed -i/cp/mv whose TARGET is under .claude (running a script from there is fine)
BASH_WRITE = re.compile(r"(>>?|\btee\b(\s+-a)?|\bsed\s+-i\S*|\bcp\b|\bmv\b|\brm\b|\btruncate\b)\s*[^|;&]*\.claude/")


def main():
    try:
        d = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    except ValueError:
        return 0
    tool, ti = d.get("tool_name", ""), d.get("tool_input") or {}
    target = ""
    if tool in WRITE_TOOLS:
        target = ti.get("file_path") or ti.get("notebook_path") or ""
        hit = "/.claude/" in target.replace("\\", "/") or target.replace("\\", "/").startswith(".claude/")
    elif tool == "Bash":
        cmd = ti.get("command", "")
        hit = bool(BASH_WRITE.search(cmd)) and "lane_obs.py" not in cmd
        target = cmd[:120]
    else:
        hit = False
    if not hit:
        return 0
    reason = (f"Refused before a permission prompt could park this lane: writes under .claude are 'sensitive files' "
              f"({target}). Task-observer records go through the helper, which picks the id and appends only: "
              f"python3 {HELPER} observation --title '<t>' --skill <s> --body-file <file in /tmp>; "
              f"python3 {HELPER} checkpoint '<text>'; python3 {HELPER} ledger 'YYYY-MM-DD | project | route | outcome'. "
              f"Any other .claude change belongs to the integrator: report it instead.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
