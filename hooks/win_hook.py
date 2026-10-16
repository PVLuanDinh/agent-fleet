#!/usr/bin/env python3
"""Run a WINDOWS Claude Code hook from a WSL Claude Code session (agent-fleet lanes), one source of truth.

The hooks stay the Windows scripts; this wrapper, run by WSL python3, translates:
  stdin  JSON: every string that is an absolute Linux path (/home, /mnt/x, /tmp, ...) -> Windows (wslpath -w)
  stdout/stderr: Windows paths (C:\\..., \\\\wsl.localhost\\<distro>\\...) -> Linux, so a lane can act on them
and returns the hook's exit code unchanged (exit 2 still blocks).

  win_hook.py ps1 <C:\\path\\hook.ps1>          PowerShell 5.1 hook
  win_hook.py claude-mem <event...>            the Windows claude-mem plugin runner (newest version), e.g.
                                               `claude-mem hook claude-code observation`
Env forwarded to Windows: AGENT_NAME, CLAUDE_CODE_*, MEMTRIM_OFF, AGENT_FLEET_*, HUB_GATE_OFF, ...
Env overrides: WIN_HOOK_WIN_HOME (Windows home as a WSL path; default: %USERPROFILE% via cmd.exe),
WIN_HOOK_NODE (Windows node.exe as a WSL path). Hook entries in WSL ~/.claude/settings.json, e.g.
  {"type": "command", "command": "python3", "args": ["<this file>", "ps1", "C:\\\\Users\\\\me\\\\.claude\\\\hooks\\\\gate.ps1"]}
A WSL lane must write under any .claude folder with Bash: Claude Code refuses Write/Edit there as a sensitive file,
so a hook message that points at skill-observations gets that note appended.
Fails open: if Windows cannot be reached, prints nothing and exits 0 (never wedges a lane).
"""
import json, os, re, subprocess, sys
from pathlib import Path

PS = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
NODE = os.environ.get("WIN_HOOK_NODE", "/mnt/c/Program Files/nodejs/node.exe")


def win_home() -> str:
    if os.environ.get("WIN_HOOK_WIN_HOME"):
        return os.environ["WIN_HOOK_WIN_HOME"]
    prof = subprocess.run(["/mnt/c/Windows/System32/cmd.exe", "/d", "/c", "echo %USERPROFILE%"], capture_output=True,
                          text=True, timeout=10, cwd="/mnt/c").stdout.strip()
    return subprocess.run(["wslpath", "-u", prof], capture_output=True, text=True, timeout=5).stdout.strip()
FORWARD = ["AGENT_NAME", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "MEMTRIM_OFF", "AGENT_FLEET_UI_OFF",
           "AGENT_FLEET_TAB_OFF", "AGENT_FLEET_PARALLEL_OFF", "HUB_GATE_OFF", "VIBEWISE_GLOBAL_OFF", "AGENT_KEEPALIVE_OFF"]
LINUX_PATH = re.compile(r"^/(home|mnt|tmp|root|usr|etc|var|opt|srv)(/[^\n\r\t]*)?$")
WIN_PATH = re.compile(r"(?<![\w/])([A-Za-z]):\\((?:[^\\\s\"'<>|*?\r\n]+\\?)*)")
UNC = re.compile(r"\\\\wsl(?:\.localhost|\$)\\[^\\]+((?:\\[^\\\s\"'<>|*?\r\n]+)*)")
_cache = {}


def to_win(s: str) -> str:
    if s in _cache:
        return _cache[s]
    try:
        out = subprocess.run(["wslpath", "-w", s], capture_output=True, text=True, timeout=5).stdout.strip() or s
    except Exception:
        out = s
    _cache[s] = out
    return out


def map_strings(obj, fn):
    if isinstance(obj, dict):
        return {k: map_strings(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [map_strings(v, fn) for v in obj]
    return fn(obj) if isinstance(obj, str) else obj


def to_linux_text(t: str) -> str:
    t = UNC.sub(lambda m: m.group(1).replace("\\", "/") or "/", t)
    return WIN_PATH.sub(lambda m: f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/"), t)


def mem_runner(args):
    cache = Path(win_home()) / ".claude" / "plugins" / "cache" / "thedotmack" / "claude-mem"
    vers = sorted((p for p in cache.glob("[0-9]*") if not (p / ".orphaned_at").exists()),
                  key=lambda p: [int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", p.name)])
    root = to_win(str(vers[-1]))
    return [NODE, root + "\\scripts\\bun-runner.js", root + "\\scripts\\worker-service.cjs", *args]


def main():
    kind, rest = sys.argv[1], sys.argv[2:]
    raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
    try:
        payload = json.dumps(map_strings(json.loads(raw), lambda s: to_win(s) if LINUX_PATH.match(s) else s))
    except ValueError:
        payload = raw
    if kind == "ps1":
        cmd = [PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", rest[0]]
    elif kind == "claude-mem":
        cmd = mem_runner(rest)
    else:
        return 0
    env = dict(os.environ)
    env["WSLENV"] = ":".join([env.get("WSLENV", "")] + [f"{v}/u" for v in FORWARD if v in env]).strip(":")
    try:
        # cwd on a Windows drive so interop does not warn about a UNC working directory
        p = subprocess.run(cmd, input=payload.encode("utf-8"), capture_output=True, timeout=600, env=env, cwd="/mnt/c/Users")
    except Exception:
        return 0
    out = p.stdout.decode("utf-8", errors="replace")
    try:
        out = json.dumps(map_strings(json.loads(out), to_linux_text)) if out.strip().startswith("{") else to_linux_text(out)
    except ValueError:
        out = to_linux_text(out)
    err = p.stderr.decode("utf-8", errors="replace")
    # PowerShell 5.1 wraps each stderr line of a native pipe in an error record and prints this marker line
    err = to_linux_text("\n".join(l for l in err.splitlines() if l.strip() != "System.Management.Automation.RemoteException"))
    lane_note = ("[WSL lane] Claude Code refuses Write/Edit under any .claude folder as a sensitive file, so write "
                 "observation files and log lines with Bash: cat > <file> <<'EOF' ... EOF, or printf '%s\\n' '<line>' >> <log>. "
                 "Append only: never sed -i or truncate a shared log.")
    if "skill-observations" in out + err and "[WSL lane]" not in out:
        if out.strip().startswith("{"):
            d = json.loads(out)
            for k in ("reason", "stopReason", "systemMessage"):
                if k in d:
                    d[k] = f"{d[k]}\n{lane_note}"
            hso = d.get("hookSpecificOutput")
            if isinstance(hso, dict) and "additionalContext" in hso:
                hso["additionalContext"] += "\n" + lane_note
            out = json.dumps(d)
        else:
            err = err + "\n" + lane_note
    sys.stdout.write(out)
    sys.stderr.write(err + ("\n" if err else ""))
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
