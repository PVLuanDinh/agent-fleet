#!/usr/bin/env python3
"""Keep the board readable: measure what agents read, release stale claims once it costs build quality.

A board is append-only and hash-chained, and score.py anchors pin its history, so nothing is deleted or rotated.
What grows without bound is the live STATE every lane reads (view.py digest): claims left behind by finished or
dead lanes. A claim is STALE when its holder has posted nothing for --idle (default 24h) and the claim is a legacy
one (no lease) or its lease expired, and the holder has no live agent-fleet session. Releasing it goes through
board.py's own owner-authorized release (`release --for <holder> --owner-auth N`), one standing owner answer
recorded in <board>/janitor.json, so every release is on the record and verify stays intact.

  janitor.py check  --board B [--budget 4000] [--stale-limit 25] [--idle 24h]   exit 1 when over a limit
  janitor.py clean  --board B [--idle 24h] [--dry-run]       release stale claims (needs owner_auth in janitor.json)
  janitor.py authorize --board B --owner-auth N              record the owner-answer seq that authorizes cleaning
Limits: the digest token budget (chars/4 of `view.py digest`) and the stale-claim count; over either, clean.
"""
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import board as B  # noqa: E402
import fastboard  # noqa: E402

fastboard.install()  # cached board reads; board.py itself stays byte-identical

NAME = "janitor"


def cfg_path(root: Path) -> Path:
    return root / "janitor.json"


def load_cfg(root: Path) -> dict:
    try:
        return json.loads(cfg_path(root).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def hours(s) -> float:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([hd]?)\s*", str(s))
    if not m:
        sys.exit(f"janitor: bad duration {s!r} (use 24h, 2d)")
    return float(m.group(1)) * (24 if m.group(2) == "d" else 1)


def epoch(ts) -> float:
    try:
        return time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0


def live_fleet_names() -> set:
    """Names of agent-fleet sessions still alive (a holder with one is never stale). Best effort."""
    if os.environ.get("JANITOR_NO_FLEET") == "1":
        return set()
    try:
        import fleet
        return {r["name"] for r in fleet.sessions() if r.get("status") != "dead" and not r.get("archived")}
    except Exception:
        return set()


def lease_of(f: Path):
    reader = getattr(B, "read_lease", None)   # lease-aware board.py; older boards have legacy claims only
    if reader:
        return reader(f)
    parts = f.read_text(encoding="utf-8").split()
    return {"who": parts[0], "ts": epoch(parts[1]) if len(parts) > 1 else None, "ttl": None, "mode": "exclusive"} if parts else None


def stale_claims(root: Path, idle_h: float, now=None) -> list:
    now = now or time.time()
    last = {}
    for m in B.good(B.load_msgs(root)):
        last[m.get("from")] = max(last.get(m.get("from"), 0.0), epoch(m.get("ts")))
    alive = live_fleet_names()
    expired = getattr(B, "expired", lambda lease, now=None: False)
    out = []
    for f in sorted((root / "claims").glob("*.claim")) if (root / "claims").is_dir() else []:
        lease = lease_of(f)
        if not lease or lease["who"] in alive:
            continue
        idle = (now - last.get(lease["who"], 0.0)) / 3600
        legacy_or_expired = lease["ttl"] is None or expired(lease, now)
        if idle > idle_h and legacy_or_expired:
            out.append({"path": unquote(f.stem), "who": lease["who"], "idle_h": round(idle, 1)})
    return out


def digest_tokens(root: Path) -> int:
    """What a lane pays to read the digest (chars / 4), measured in-process on the shared cache."""
    import contextlib, io
    import view as V
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        V.main(["--board", str(root), "digest"])
    return len(buf.getvalue()) // 4


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # claim paths can hold chars a cp1252 pipe cannot encode
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog="janitor", description=__doc__.splitlines()[0])
    ap.add_argument("--board"); ap.add_argument("--project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--budget", type=int); c.add_argument("--stale-limit", type=int)
    c.add_argument("--idle")
    k = sub.add_parser("clean"); k.add_argument("--idle"); k.add_argument("--dry-run", action="store_true")
    au = sub.add_parser("authorize"); au.add_argument("--owner-auth", type=int, required=True)
    a = ap.parse_args(argv)
    root = B.board_root(a)
    cfg = load_cfg(root)
    idle_h = hours(getattr(a, "idle", None) or cfg.get("idle", "24h"))

    if a.cmd == "authorize":
        m = next((x for x in B.good(B.load_msgs(root)) if x["seq"] == a.owner_auth), None)
        if not m or m.get("kind") != "owner-answer" or m.get("from") != B.OWNER:
            print(f"janitor: #{a.owner_auth} is not an owner-answer", file=sys.stderr); return 1
        cfg["owner_auth"] = a.owner_auth
        cfg_path(root).write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        print(f"janitor: cleaning authorized by owner-answer #{a.owner_auth}"); return 0

    if a.cmd == "check":
        budget = a.budget or cfg.get("budget", 4000)
        limit = a.stale_limit if a.stale_limit is not None else cfg.get("stale_limit", 25)
        tok, stale = digest_tokens(root), stale_claims(root, idle_h)
        # Over budget only counts when there is something to clean; active threads are not the janitor's to shrink.
        over = len(stale) > limit or (tok > budget and bool(stale))
        note = " (over budget from ACTIVE threads; nothing to clean)" if tok > budget and not stale else ""
        print(f"janitor: digest ~{tok} tokens (budget {budget}); stale claims {len(stale)} (limit {limit}, idle >{idle_h:g}h)"
              + (" -> CLEAN DUE" if over else " -> ok" + note))
        return 1 if over else 0

    # clean
    stale = stale_claims(root, idle_h)
    if a.dry_run:
        for s in stale:
            print(f"  would release {s['path']} held by {s['who']} (idle {s['idle_h']}h)")
        print(f"janitor: {len(stale)} stale claim(s) (dry run)"); return 0
    auth = cfg.get("owner_auth")
    if auth is None:
        print("janitor: no owner authorization recorded; ask the owner, then `janitor.py authorize --owner-auth N`",
              file=sys.stderr); return 1
    import contextlib, io
    done = failed = 0
    for s in stale:
        with contextlib.redirect_stdout(io.StringIO()):   # one summary line, not one per claim
            rc = B.main(["--board", str(root), "release", s["path"], "--as", NAME, "--for", s["who"],
                         "--owner-auth", str(auth), "--thread", "janitor"])
        done += rc == 0; failed += rc != 0
    probs = B.verify(root)
    print(f"janitor: released {done} stale claim(s), {failed} failed; verify {'INTACT' if not probs else 'BROKEN: ' + probs[0]}")
    return 0 if not failed and not probs else 1


if __name__ == "__main__":
    sys.exit(main())
