#!/usr/bin/env python3
"""One health report for a board and its fleet: what is broken, stale or stuck, in a single read. Read-only.

  doctor.py --board B [--lock DIR ...] [--idle 24h] [--stale-limit N] [--lock-grace 10m]

RED lines make the exit code 1; OK and INFO never do.
  verify       the hash chain (board.py verify)
  claims       stale claims (janitor's rule: holder idle > --idle, claim legacy or expired) over --stale-limit
  locks        each lock dir (--lock, plus the project's "locks" in fleet-requirements.json): RED when the pid in its
               owner.txt is gone (a crashed heavy run), or it has no owner.txt past --lock-grace
  sessions     live agent-fleet sessions parked on a prompt (fleet.py sessions); DOCTOR_NO_FLEET=1 skips it
  lane guards  the project's REQUIRED preflight rows that failed at their last check (fleet-preflight-cache.json)
  digest       what a lane pays to read `view.py digest`, against janitor's budget (INFO)
  open items   open challenges and owner questions (INFO)
It repairs nothing. Each repair stays with whoever is authorized to make it: janitor.py clean (needs an owner answer),
the lock's owner, the lead.
"""
import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import board as B  # noqa: E402
import fastboard  # noqa: E402

fastboard.install()  # cached board reads; board.py itself stays byte-identical


def pid_alive(pid: int):
    """True / False, or None when this host cannot tell. A lock owner is a Windows pid (PowerShell $PID) or a Linux
    pid (a lane script in WSL), so it is alive when EITHER side knows it: tasklist (tasklist.exe from WSL) or /proc.
    ponytail: a Linux and a Windows pid with the same number read alive, so a stale lock can hide behind a collision
    (reported as held, never wrongly RED). Upgrade path: an `os` line in owner.txt."""
    if Path(f"/proc/{pid}").exists():
        return True
    exe = shutil.which("tasklist") or shutil.which("tasklist.exe")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return str(pid) in out.split()


def check_lock(d: Path, grace: int, now: float) -> tuple:
    if not d.exists():
        return "OK", f"{d.name} free"
    owner = d / "owner.txt"
    if not owner.exists():
        age = now - d.stat().st_mtime
        if age > grace:
            return "RED", f"{d} has no owner.txt for {age / 60:.0f} min: a crashed take; remove it by hand once nothing runs"
        return "INFO", f"{d.name} being taken ({age:.0f}s old, no owner.txt yet)"
    kv = dict(line.split(None, 1) for line in owner.read_text(encoding="utf-8", errors="replace").splitlines()
              if len(line.split(None, 1)) == 2)
    who, start = kv.get("name", "?"), kv.get("start", "?")
    pid = int(kv["pid"]) if kv.get("pid", "").strip().isdigit() else 0
    alive = pid_alive(pid) if pid else False
    if alive:
        return "OK", f"{d.name} held by {who} pid {pid} since {start}"
    if alive is None:
        return "INFO", f"{d.name} held by {who} pid {pid} since {start} (this host cannot check the pid)"
    return "RED", f"stale lock {d}: owner {who} pid {pid} is gone (taken {start}); remove it by hand once nothing runs"


def project_of(root: Path) -> dict:
    """The requirements project whose board IS this one. samefile, not a string compare: WSL paths are case-sensitive
    strings over a case-insensitive NTFS board, so /mnt/c/users/... must still find the project (and its locks)."""
    import fleet
    from roster import native
    try:
        projects = fleet.load_requirements().get("projects", [])
    except fleet.RequirementsError:
        return {}
    def same(p):
        try:
            return os.path.samefile(native(p), root)
        except OSError:
            return False
    return next((p for p in projects if p.get("board") and same(p["board"])), {})


def report(a, root: Path) -> list:
    import janitor as J
    rows, now = [], time.time()
    probs = B.verify(root)
    rows.append(("RED" if probs else "OK", "verify", f"BROKEN: {probs[0]} (+{len(probs) - 1} more)" if probs else "INTACT"))
    cfg = J.load_cfg(root)
    idle = J.hours(a.idle or cfg.get("idle", "24h"))
    limit = a.stale_limit if a.stale_limit is not None else cfg.get("stale_limit", 25)
    stale = J.stale_claims(root, idle)
    detail = f"{len(stale)} stale (limit {limit}, idle >{idle:g}h)" + (
        ": " + ", ".join(f"{s['path']} ({s['who']})" for s in stale[:10]) + (" ..." if len(stale) > 10 else "") if stale else "")
    rows.append(("RED" if len(stale) > limit else "OK", "claims", detail))
    proj = project_of(root)
    from roster import native
    grace = B.parse_ttl(a.lock_grace)
    for lk in list(a.lock or []) + list(proj.get("locks", [])):
        lvl, msg = check_lock(Path(native(lk)), grace, now)
        rows.append((lvl, "locks", msg))
    if os.environ.get("DOCTOR_NO_FLEET") == "1":
        rows.append(("INFO", "sessions", "skipped (DOCTOR_NO_FLEET=1)"))
    else:
        import fleet
        try:
            live = [r for r in fleet.sessions() if r.get("status") != "dead" and not r.get("archived")]
            stuck = [f"{r['name']} ({r['blocked']})" for r in live if r.get("blocked")]
            rows.append(("RED" if stuck else "OK", "sessions",
                         f"{len(live)} live" + (f"; BLOCKED: {', '.join(stuck)}" if stuck else "")))
        except Exception as e:
            rows.append(("INFO", "sessions", f"unreadable ({type(e).__name__}: {str(e)[:120]})"))
    if proj:
        cache = Path.home() / ".claude" / "fleet-preflight-cache.json"
        try:
            last = {r["skill"]: r for r in json.loads(cache.read_text(encoding="utf-8")).get(proj.get("name", "?"), {}).get("rows", [])}
        except (OSError, ValueError):
            last = {}
        bad = [s["skill"] for s in proj.get("skills", []) if s.get("required", True)
               and s["skill"] in last and not last[s["skill"]]["ok"]]
        unchecked = [s["skill"] for s in proj.get("skills", []) if s["skill"] not in last]
        rows.append(("RED" if bad else "OK", "lane guards", (f"failing: {', '.join(bad)}" if bad else "required rows pass")
                     + (f"; never checked: {', '.join(unchecked)}" if unchecked else "")))
    else:
        rows.append(("INFO", "lane guards", "no fleet-requirements project uses this board"))
    rows.append(("INFO", "digest", f"~{J.digest_tokens(root)} tokens (budget {cfg.get('budget', 4000)})"))
    msgs = B.good(B.load_msgs(root))
    answered = {m.get("reply_to") for m in msgs if m.get("kind") == "owner-answer"}
    open_q = sum(1 for m in msgs if m.get("kind") == "owner-question" and m["seq"] not in answered)
    try:
        import review as R
        n_ch = len(R._state(msgs)[2])
    except ImportError:
        n_ch = "?"
    rows.append(("INFO", "open items", f"{n_ch} open challenge(s), {open_q} open owner question(s)"))
    return rows


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # board text holds chars a cp1252 pipe cannot encode
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog="doctor", description=__doc__.splitlines()[0])
    ap.add_argument("--board"); ap.add_argument("--project")
    ap.add_argument("--lock", action="append", help="a lock dir with owner.txt (name/pid/start lines); repeatable")
    ap.add_argument("--idle"); ap.add_argument("--stale-limit", type=int)
    ap.add_argument("--lock-grace", default="10m", help="how long a lock dir may lack owner.txt (default 10m)")
    a = ap.parse_args(argv)
    root = B.board_root(a)
    rows = report(a, root)
    for lvl, check, msg in rows:
        print(f"{lvl:4} {check}: {msg}")
    red = sum(1 for r in rows if r[0] == "RED")
    print(f"doctor: {'HEALTHY' if not red else f'{red} RED'} ({root})")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
