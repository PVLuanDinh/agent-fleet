#!/usr/bin/env python3
"""Lane hook (PostToolUse, Stop, SessionEnd): keep a working lane's own claims alive, tell it when board mail is
addressed to it, and record its last event for `fleet.py resurrect`.

Wired into WSL ~/.claude/settings.json, so it runs in every WSL session; anything that is not a lane gets silence.
Which lane: $AGENT_NAME + $AGENT_BOARD; else, inside an agent-fleet session, only the roster entry whose id is
$AGENT_FLEET_SESSION_ID (agent-fleet exports it into each session); else (no session id) the one roster entry whose
folder holds the cwd. Two entries for one folder (panel lenses share one) is ambiguous: silence, never another lane's
name.
1. Leases: each of the lane's claims (and request takes) whose lease ends within half of LANEHOOK_TTL (default 30m)
   is renewed through
   board.py's own `renew` (on the record) to max(its own ttl, LANEHOOK_TTL): a lease is never shortened. A lane that
   stops calling tools stops renewing, and its claims lapse to the next claimer with no owner answer. ttl 0 and
   pre-lease claims are left alone: whoever set them chose no expiry.
2. Mail: messages addressed to the lane past its `view.py brief` cursor are announced once each. A peek: the cursor
   is never advanced, so `brief` stays the way a lane reads its mail.
3. Heartbeat: roster.beat(lane, "tool" | "stop" | "end"). Last event "tool" + no live session = cut off by a crash or a
   reboot, which `fleet.py resurrect --all` brings back; a lane that finished its turn ("stop") or ended is left down.
PostToolUse work runs at most once per LANEHOOK_EVERY seconds (default 60) per lane, and the throttle is saved BEFORE
the work, so a slow board can never make every tool call pay again. Board reads go through fastboard's cache (an
uncached read of a 6,000-message board over /mnt/c took 53 s). Fails open: any error prints nothing and exits 0.
"""
import json, os, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import roster  # noqa: E402  (cheap: everything heavier is imported only once a lane is found)

MAX_SHOWN = 5   # mail lines shown per alert; the brief line reads the rest


def _norm(p: str) -> str:
    return os.path.normpath(roster.native(str(p))).replace("\\", "/").rstrip("/").lower()


def find_lane(cwd: str):
    """(name, board root) of the lane running here, or None (not a lane, or not a unique one)."""
    if os.environ.get("AGENT_NAME") and os.environ.get("AGENT_BOARD"):
        return os.environ["AGENT_NAME"], Path(roster.native(os.environ["AGENT_BOARD"]))
    lanes = roster.load()
    sid = os.environ.get("AGENT_FLEET_SESSION_ID")
    if sid:
        # Inside an agent-fleet session the id decides alone: an unrecorded lane below a recorded folder stays silent
        # instead of borrowing that folder's lane name.
        hit = [(n, e) for n, e in lanes.items() if e.get("id") == sid]
        return (hit[0][0], Path(roster.native(hit[0][1]["board"]))) if len(hit) == 1 else None
    c, best, tie = _norm(cwd), None, False
    for name, e in lanes.items():
        d = _norm(e.get("dir", ""))
        if d and (c == d or c.startswith(d + "/")):
            if best is None or len(d) > len(best[2]):
                best, tie = (name, e["board"], d), False
            elif len(d) == len(best[2]):
                tie = True
    return None if best is None or tie else (best[0], Path(roster.native(best[1])))


def due_renewals(B, root: Path, name: str, ttl: int, now: float) -> list:
    from urllib.parse import unquote
    out, cdir = [], root / "claims"
    if not cdir.is_dir():
        return out
    held = [(f, unquote(f.stem), False) for f in cdir.glob("*.claim")]
    held += [(d / name, unquote(d.stem), True) for d in cdir.glob("*.shared") if (d / name).exists()]
    for f, path, shared in held:
        lease = B.read_lease(f)
        if lease and lease["who"] == name and lease["ttl"] and lease["ts"] is not None \
                and lease["ts"] + lease["ttl"] - now <= ttl / 2:
            out.append((path, shared, max(lease["ttl"], ttl)))
    return out


def due_takes(B, root: Path, name: str, ttl: int, now: float) -> list:
    """The lane's own request takes (review.py take) that end within half of ttl: (file, renewed ttl)."""
    tdir, out = root / "takes", []
    for f in (sorted(tdir.iterdir()) if tdir.is_dir() else []):
        lease = B.read_lease(f) if f.is_file() else None
        if lease and lease["who"] == name and lease["ttl"] and lease["ts"] is not None \
                and lease["ts"] + lease["ttl"] - now <= ttl / 2:
            out.append((f, max(lease["ttl"], ttl)))
    return out


def keep_alive(B, root: Path, name: str, ttl: int, now: float) -> tuple:
    """Renew the lane's due claims (on the record, through board.py renew) and its due takes. (renewed, failures)."""
    claims = due_renewals(B, root, name, ttl, now)
    failed = renew(B, root, name, claims)
    takes = due_takes(B, root, name, ttl, now)
    for f, t in takes:
        B.write_atomic(f, B.lease_text(name, t, "take"))
    return len(claims) - len(failed) + len(takes), failed


def renew(B, root: Path, name: str, due: list) -> list:
    import contextlib, io
    failed = []
    for path, shared, ttl in due:
        argv = ["--board", str(root), "renew", path, "--as", name, "--ttl", str(ttl), "--thread", "lanehook"]
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            try:
                rc = B.main(argv + (["--shared"] if shared else []))
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 1
        if rc:
            failed.append(f"{path}: {err.getvalue().strip() or f'rc {rc}'}")
    return failed


def unread_mail(B, root: Path, name: str):
    """(cursor, messages addressed to this lane past the cursor)."""
    try:
        cur = int((root / "cursors" / name).read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        cur = 0
    return cur, [m for m in B.good(B.load_msgs(root)) if m["seq"] > cur and m.get("to") == name and m.get("from") != name]


def run(payload: dict) -> str:
    lane = find_lane(payload.get("cwd") or os.getcwd())
    if not lane:
        return ""
    name, root = lane
    event = payload.get("hook_event_name")
    if event in ("Stop", "SessionEnd"):
        roster.beat(name, "stop" if event == "Stop" else "end")   # never throttled: these mark a clean finish
        return ""
    roster.beat(name, "tool")
    state_f = Path(os.environ.get("LANEHOOK_STATE") or tempfile.gettempdir()) / f"agent-fleet-lanehook-{name}.json"
    try:
        state = json.loads(state_f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    now = time.time()
    if now - state.get("ran", 0) < int(os.environ.get("LANEHOOK_EVERY", "60")):
        return ""
    state["ran"] = now
    state_f.parent.mkdir(parents=True, exist_ok=True)
    state_f.write_text(json.dumps(state), encoding="utf-8")
    import board as B
    import fastboard
    fastboard.install()
    ttl = int(os.environ.get("LANEHOOK_TTL", "1800"))
    notes = [f"lanehook: could not renew {f}" for f in keep_alive(B, root, name, ttl, now)[1]]
    cur, mine = unread_mail(B, root, name)
    if mine and mine[-1]["seq"] > state.get("alerted", 0):
        notes.append(f"board: {len(mine)} new message(s) addressed to you ({name}) past your brief cursor #{cur}:")
        notes += [f"  #{m['seq']} {m.get('from')} {m.get('kind')}: {' '.join(str(m.get('body', '')).split())[:160]}"
                  for m in mine[-MAX_SHOWN:]]
        notes.append(f"Read them: python ~/.claude/skills/agent-fleet/scripts/view.py --board {root.as_posix()} "
                     f"brief --as {name} --to-me")
        state["alerted"] = mine[-1]["seq"]
        state_f.write_text(json.dumps(state), encoding="utf-8")
    return "\n".join(notes)


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
        text = run(json.loads(sys.stdin.read() or "{}"))
        if text:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
