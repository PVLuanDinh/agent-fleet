#!/usr/bin/env python3
"""Bridge from the board to agent-manager, the tmux agent fleet (github.com/YoanWai/agent-manager).

agent-manager is Linux/macOS only, so on Windows every call goes through `wsl.exe -d <distro>`.

  fleet.py am <args...>        run agent-manager (TUI with no args: needs a real terminal)
  fleet.py leases [--json]     live agent-manager file leases (read-only, from its state.db)
  fleet.py mcp-config          the stdio MCP command that lets a Desktop/CLI session drive the fleet
  fleet.py --board DIR on|off  (or --project NAME) join or leave one claim domain: with `on`, board.py claim also
                               refuses a path an agent-manager session holds an exclusive lease on

Env: AGENT_FLEET_WSL_DISTRO (default Ubuntu); AGENT_FLEET_AM_DB reads that state.db file
directly (tests, or a non-default agent-manager config dir).

ponytail: leases are matched on the path text only, not on which checkout the agent-manager
session runs in, so two repos with the same relative path collide. A false refusal, never a
missed one. Upgrade path: compare against the session's repo column in state.db.
"""
import argparse, fnmatch, json, os, shutil, sqlite3, subprocess, sys, time
from pathlib import Path

DISTRO = os.environ.get("AGENT_FLEET_WSL_DISTRO", "Ubuntu")
MARKER = "fleet.on"
LEASE_SQL = ("SELECT pattern, mode, session_id, note, expires_at FROM file_reservations "
             "WHERE expires_at > ? ORDER BY acquired_at")
# Runs inside WSL; prints NODB when agent-manager has never run there, so "no fleet" is not an error.
WSL_LEASES = (
    "import json,os,sqlite3,sys,time\n"
    "d=os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')\n"
    "p=os.path.join(d,'agent-manager','state.db')\n"
    "if not os.path.exists(p): print('NODB'); sys.exit(0)\n"
    "c=sqlite3.connect('file:'+p+'?mode=ro',uri=True)\n"
    "print(json.dumps(c.execute(sys.argv[1],(time.time_ns(),)).fetchall()))\n")


def wsl_prefix() -> list:
    # --exec, not --: `wsl.exe --` re-joins argv into one shell string and loses the quoting.
    return [] if os.name != "nt" else ["wsl.exe", "-d", DISTRO, "--exec"]


def read_db(path: str) -> list:
    c = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        return c.execute(LEASE_SQL, (time.time_ns(),)).fetchall()
    finally:
        c.close()


def leases(strict: bool = False) -> list:
    """Live leases as dicts. Raises RuntimeError when they cannot be read; with strict (the board
    joined the fleet), a missing wsl.exe or agent-manager state also raises instead of reading as none."""
    if os.environ.get("AGENT_FLEET_AM_DB"):
        rows = read_db(os.environ["AGENT_FLEET_AM_DB"])
    else:
        if os.name == "nt" and not shutil.which("wsl.exe"):
            if strict:
                raise RuntimeError("fleet: board joined the fleet but wsl.exe is not on PATH")
            return []
        # Login shell, like agent-manager itself, so XDG_CONFIG_HOME from the profile applies.
        p = subprocess.run(wsl_prefix() + ["bash", "-lc", 'exec python3 -c "$1" "$2"', "fleet", WSL_LEASES, LEASE_SQL],
                           capture_output=True, text=True, timeout=60)
        out = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
        if p.returncode != 0 or not out:
            raise RuntimeError(f"fleet: cannot read agent-manager leases (rc={p.returncode}): {p.stderr.strip()[:300]}")
        if out == "NODB":
            if strict:
                raise RuntimeError(f"fleet: board joined the fleet but agent-manager has no state in WSL distro {DISTRO}")
            return []
        rows = json.loads(out)
    return [dict(zip(("pattern", "mode", "session_id", "note", "expires_at"), r)) for r in rows]


def norm(p: str) -> str:
    p = p.strip().replace("\\", "/").lower()
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def overlaps(path: str, pattern: str) -> bool:
    a, b = norm(path), norm(pattern)
    parts = b.lstrip("/").split("/")
    # An absolute lease names the repo by its location: try every tail of it as repo-relative.
    tails = ["/".join(parts[i:]) for i in range(len(parts))] if b.startswith("/") else [b]
    for t in tails:
        for g in {t, t[3:] if t.startswith("**/") else t}:
            if (a == g or fnmatch.fnmatchcase(a, g) or fnmatch.fnmatchcase(g, a)
                    or a.startswith(g + "/") or g.startswith(a + "/")):
                return True
    return False


def conflicts(path: str, root: Path) -> list:
    """Exclusive agent-manager leases over `path`; empty unless the board joined the fleet."""
    if not (root / MARKER).exists():
        return []
    return [l for l in leases(strict=True) if l["mode"] != "shared" and overlaps(path, l["pattern"])]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fleet", description=__doc__.splitlines()[0])
    ap.add_argument("--board"); ap.add_argument("--project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("am", add_help=False)
    sub.add_parser("leases").add_argument("--json", action="store_true")
    sub.add_parser("mcp-config")
    sub.add_parser("on"); sub.add_parser("off")
    args, rest = ap.parse_known_args(argv)

    if args.cmd == "am":
        # bash -lc: agent-manager lives in ~/.local/bin, which only a login shell puts on PATH.
        return subprocess.call(wsl_prefix() + ["bash", "-lc", 'exec agent-manager "$@"', "am"] + rest)
    if rest:
        ap.error(f"unrecognized arguments: {' '.join(rest)}")
    if args.cmd == "leases":
        ls = leases()
        for l in ls:
            print(json.dumps(l) if args.json else f"{l['mode']:9} {l['pattern']}  ({l['session_id']}) {l['note']}")
        if not args.json:
            print(f"{len(ls)} live lease(s)")
        return 0
    if args.cmd == "mcp-config":
        cmd = wsl_prefix() + ["bash", "-lc", "exec agent-manager mcp"]
        print(json.dumps({"agent-manager": {"type": "stdio", "command": cmd[0], "args": cmd[1:]}}, indent=2))
        print("claude mcp add -s user agent-manager -- " + " ".join(
            c if " " not in c else f"'{c}'" for c in cmd))
        return 0
    if args.cmd in ("on", "off"):
        if not (args.board or args.project or os.environ.get("AGENT_BOARD")):
            ap.error("on/off need --board DIR or --project NAME (the board everyone was handed)")
        import board
        root = board.board_root(args); m = root / MARKER
        if args.cmd == "on":
            try:
                leases(strict=True)
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError, sqlite3.Error) as e:
                print(f"fleet: not joined, leases unreadable: {e}", file=sys.stderr); return 1
            m.parent.mkdir(parents=True, exist_ok=True); m.write_text("1\n", encoding="utf-8")
        elif m.exists():
            m.unlink()
        print(f"fleet claim domain {args.cmd} for {root}"); return 0


if __name__ == "__main__":
    sys.exit(main())
