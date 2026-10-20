#!/usr/bin/env python3
"""Token-lean reads of the board: the CURRENT STATE instead of the whole log, one line per message,
only what is new to you, full text only on demand. Read-only; never posts.

  view.py digest [--all]               the board state: latest line per thread active in 48h, claims
                                       of holders active in 24h, open owner questions and challenges;
                                       older state collapses to counts (--all lists everything)
  view.py brief --as R1 [--to-me]      one line per message you have not seen yet (a per-agent cursor
                                       under <board>/cursors/); --all ignores the cursor
  view.py show N [N ...]               full text of chosen messages

Why: a full `board.py read` re-sends every message ever posted (measured on a production board,
2026-10-08: ~6.9k tokens for 130 messages, and growing). Shared state beats a shared log
(blackboard MAS; PACT's compact action-state records); references beat copies (Anthropic's
multi-agent research system: store outputs, pass lightweight references).

ponytail: a separate script because score.py pins board.py's hash at task open. The cursor is a
plain file outside msgs/, so it is not part of the hash chain; deleting it only means re-reading.
"""
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board as B  # noqa: E402
import fastboard  # noqa: E402

fastboard.install()  # cached board reads; board.py itself stays byte-identical

WIDTH = 140


def _line(m):
    to = f">{m['to']}" if m.get("to") else ""
    tags = "".join(f" {k}={m[k]}" for k in ("verdict", "claim", "reply_to", "path") if m.get(k))
    body = " ".join(str(m.get("body", "")).split())
    if len(body) > WIDTH:
        body = body[: WIDTH - 1] + "~"
    return f"#{m['seq']} {m.get('from')}{to} {m.get('kind')}[{m.get('thread') or '-'}]{tags}: {body}"


RECENT_H = 48   # digest lists threads active this recently; older ones collapse to a count (--all lists them)
ACTIVE_H = 24   # a claim is listed when its holder posted this recently; stale holders collapse to counts


def _epoch(ts):
    import time
    try:
        return time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0


def cmd_digest(a, root):
    # Bounded: the full state grew to ~19k tokens on a 2,950-message board and every lane read it.
    import time
    from collections import Counter
    now = time.time()
    msgs = B.good(B.load_msgs(root))
    latest, last_seen = {}, {}
    for m in msgs:
        last_seen[m.get("from")] = _epoch(m.get("ts"))
        if m.get("kind") in ("claim", "release", "review", "renew"):
            continue
        latest[m.get("thread") or "-"] = m
    recent = {t: m for t, m in latest.items() if a.all or now - _epoch(m.get("ts")) <= RECENT_H * 3600}
    print(f"head #{msgs[-1]['seq'] if msgs else 0}  messages {len(msgs)}  threads {len(latest)}")
    for thread, m in sorted(recent.items()):
        print("  " + _line(m))
    if len(recent) < len(latest):
        print(f"  ... {len(latest) - len(recent)} quiet thread(s), no message for >{RECENT_H}h (view.py digest --all)")
    cdir = root / "claims"
    claims = sorted(cdir.glob("*.claim")) if cdir.exists() else []
    if claims:
        print(f"claims ({len(claims)}):")
        stale = Counter()
        for p in claims:
            holder = p.read_text(encoding="utf-8").split()[0] if p.stat().st_size else "?"
            if a.all or now - last_seen.get(holder, 0) <= ACTIVE_H * 3600:
                print(f"  {holder}: {_unq(p.stem)}")
            else:
                stale[holder] += 1
        if stale:
            print(f"  ... {sum(stale.values())} claim(s) by holders idle >{ACTIVE_H}h: "
                  + ", ".join(f"{h} {n}" for h, n in stale.most_common(6))
                  + (f", +{len(stale) - 6} more" if len(stale) > 6 else "") + "  (janitor.py check)")
    answered = {m.get("reply_to") for m in msgs if m.get("kind") == "owner-answer"}
    open_q = [m for m in msgs if m.get("kind") == "owner-question" and m["seq"] not in answered]
    if open_q:
        print(f"owner questions open ({len(open_q)}): " + ", ".join(f"#{m['seq']} {m.get('from')}" for m in open_q))
    try:
        import review as R
        _, _, challenges, _ = R._state(msgs)
        if challenges:
            print(f"challenges open ({len(challenges)}): " + ", ".join(
                f"#{c['seq']} {c.get('from')} on #{c.get('reply_to')}" for c in challenges.values()))
    except ImportError:
        pass
    return 0


def _unq(stem):
    from urllib.parse import unquote
    return unquote(stem)


def cmd_brief(a, root):
    who = B.author_of(a)
    cur = root / "cursors" / who
    since = 0
    if not a.all and cur.exists():
        try:
            since = int(cur.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            since = 0
    msgs = [m for m in B.good(B.load_msgs(root)) if m["seq"] > since]
    if a.to_me:
        msgs = [m for m in msgs if m.get("to") in (who, "all", None) or m.get("from") == who]
    i = 0
    while i < len(msgs):
        # A run of releases by one author (a janitor clean-up) is one line, not hundreds in every agent's brief.
        j = i
        while (j + 1 < len(msgs) and msgs[j + 1].get("kind") == "release" == msgs[i].get("kind")
               and msgs[j + 1].get("from") == msgs[i].get("from")):
            j += 1
        if j - i >= 2:
            print(f"#{msgs[i]['seq']}-#{msgs[j]['seq']} {msgs[i].get('from')} release: {j - i + 1} claims released "
                  f"(view.py show {msgs[i]['seq']} for one)")
        else:
            for m in msgs[i:j + 1]:
                print(_line(m))
        i = j + 1
    new_head = max((m["seq"] for m in B.good(B.load_msgs(root))), default=since)
    cur.parent.mkdir(parents=True, exist_ok=True)
    cur.write_text(str(new_head), encoding="utf-8")
    print(f"({len(msgs)} new since #{since}; cursor now #{new_head}; `view.py show N` for full text)")
    return 0


def cmd_show(a, root):
    by = {m["seq"]: m for m in B.good(B.load_msgs(root))}
    for n in a.n:
        print(B.show(by[n]) if n in by else f"#{n}: no such message")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="view", description=__doc__.splitlines()[0])
    ap.add_argument("--board"); ap.add_argument("--project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("digest").add_argument("--all", action="store_true", help="every thread and claim, unbounded")
    b = sub.add_parser("brief"); b.add_argument("--as", dest="as_"); b.add_argument("--to-me", action="store_true")
    b.add_argument("--all", action="store_true")
    s = sub.add_parser("show"); s.add_argument("n", type=int, nargs="+")
    a = ap.parse_args(argv)
    root = B.board_root(a)
    return {"digest": cmd_digest, "brief": cmd_brief, "show": cmd_show}[a.cmd](a, root)


if __name__ == "__main__":
    sys.exit(main())
