#!/usr/bin/env python3
"""Peer review while idle: agents with no work of their own review the other agents' plans and
results, from their own context, and argue it out on the board until each challenge is settled.

  review.py submit --as G3 --thread G3 "<plan text>"        post a plan for review (kind plan)
  review.py next   --as R1 [--min 2] [--wait SEC]            reserve + print the next item to review
  review.py post   --as R1 --on N --verdict agree|challenge --cite "<evidence>" "<text>"
  review.py reply  --as G3 --to C "<answer to challenge C>"  the author (or anyone) argues back
  review.py resolve --as R1 --challenge C "<why settled>"     the challenger closes it (lead may overrule)
  review.py open                                             list unresolved challenges
  review.py check TASK [--min N]                             reviewed=1 only if every plan/result in the
                                                             task's thread has >= N agree reviews by
                                                             others and no open challenge; exit 0/1
  review.py requests [--as R1]                               open `request` messages nobody has taken
                                                             (with --as: only those R1 may take)
  review.py take N --as R1 [--ttl 4h]                        take request N: exactly one taker wins
                                                             (O_EXCL, like claims); posted on the record; a
                                                             lease: an expired take reopens the request

Reviewable items: kind `result` (board.py post --kind result) and kind `plan` (submit above).
Reviews post under thread "review" with reply_to = the item, so review text never counts toward a
task thread's scope check in score.py.

ponytail: a separate script on purpose -- score.py pins its own and board.py's hashes at `open`,
so editing either would zero integrity on every task already open. Identity is self-declared
(same OS user): "a different name" is the reviewer-independence check, not proof of a different agent.
Upgrade path: fold `check` into score.py as a vector dimension at a quiet point, re-opening tasks.
"""
import argparse, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board as B  # noqa: E402
import fastboard  # noqa: E402

fastboard.install()  # cached board reads; board.py itself stays byte-identical

REVIEWABLE = ("result", "plan")
VERDICTS = ("agree", "challenge", "answer", "resolve")
RESERVE_S = 45 * 60  # a reservation not turned into a review within this is free again
LEAD = "lead"


def _msgs(root):
    return B.good(B.load_msgs(root))


def _post(root, who, kind, body, **kw):
    """board.post with review.py's own kinds allowed for this call only (no leak into board.KINDS)."""
    added = kind not in B.KINDS
    if added:
        B.KINDS.add(kind)
    try:
        return B.post(root, who, kind, body, **kw)
    finally:
        if added:
            B.KINDS.discard(kind)


def _state(msgs):
    """items {seq: msg}, reviews_of {item: [reviews]}, open challenges {seq: msg}, overruled {seq: msg}.
    Authority is re-checked HERE, not only in the commands, so a hand-written board message cannot
    retract someone else's work or close someone else's challenge."""
    by = {m["seq"]: m for m in msgs}
    retracted = {m.get("reply_to") for m in msgs if m.get("kind") == "retract"
                 and by.get(m.get("reply_to"), {}).get("from") == m.get("from")}  # own messages only
    items = {m["seq"]: m for m in msgs if m.get("kind") in REVIEWABLE}
    reviews_of = {s: [] for s in items}
    challenges, overruled = {}, {}
    for r in (m for m in msgs if m.get("kind") == "review" and m["seq"] not in retracted):
        v, to = r.get("verdict"), r.get("reply_to")
        if v in ("agree", "challenge") and to in reviews_of:
            reviews_of[to].append(r)
            if v == "challenge":
                challenges[r["seq"]] = r
        elif v == "resolve" and to in challenges:
            c = challenges[to]
            if r.get("from") == c.get("from"):
                challenges.pop(to)  # the challenger is convinced
            elif r.get("from") == LEAD and items[c["reply_to"]].get("from") != LEAD:
                overruled[to] = challenges.pop(to)  # recorded; check() reds it unless accepted
    for s in retracted:  # a retracted item still under challenge stays, so check() still sees it
        if s in items and not any(c["reply_to"] == s for c in challenges.values()):
            items.pop(s)
    return items, reviews_of, challenges, overruled


def _reserve_dir(root):
    d = root / "reviewing"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _live_reservations(root, seq):
    now, out = time.time(), set()
    for p in _reserve_dir(root).glob(f"{seq}-*.lock"):
        try:
            if now - p.stat().st_mtime < RESERVE_S:
                out.add(p.stem.split("-", 1)[1])
            else:
                p.unlink(missing_ok=True)  # expired: the slot is free again
        except OSError:
            pass
    return out


def _pick(root, who, min_reviews, skip=()):
    items, reviews_of, challenges, _ = _state(_msgs(root))
    open_on = {c["reply_to"] for c in challenges.values()}
    best = None
    for seq, item in sorted(items.items()):
        if item.get("from") == who or seq in skip:
            continue
        done = {r.get("from") for r in reviews_of[seq]}
        busy = _live_reservations(root, seq)
        if who in done or who in busy:
            continue
        agrees = sum(1 for r in reviews_of[seq] if r.get("verdict") == "agree")
        if agrees + len(busy - done) >= min_reviews and seq not in open_on:
            continue  # enough eyes already, and nothing contested
        key = (agrees + len(busy), seq)
        if best is None or key < best[0]:
            best = (key, seq)
    return None if best is None else best[1]


def cmd_next(a, root):
    who = B.author_of(a)
    deadline = time.time() + max(0, a.wait)
    skip = set()
    while True:
        seq = _pick(root, who, a.min, skip)
        if seq is not None:
            lock = _reserve_dir(root) / f"{seq}-{who}.lock"
            try:
                fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                os.close(fd)
            except FileExistsError:
                skip.add(seq)  # an undeletable stale lock: skip it this call, never spin on it
                continue
            items, reviews_of, _, _ = _state(_msgs(root))
            print(f"REVIEW #{seq} (reserved for {who} for {RESERVE_S // 60} min)")
            print(B.show(items[seq]))
            # BLIND by design: prior reviews stay hidden until you post yours. Peers' rationales
            # drive conformity and talk agents out of correct answers ("The Cost of Consensus",
            # arXiv 2605.00914: majority adoption up to 85.5%, correct answers destabilized up to 70%).
            print(f"({len(reviews_of[seq])} prior review(s) hidden until you post yours; then `view.py show` them)")
            print("Re-derive it from your own context (re-run the cite, read the code), then:\n"
                  f"  review.py post --as {who} --on {seq} --verdict agree|challenge --cite \"<what you ran/read>\" \"<finding>\"")
            return 0
        if time.time() >= deadline:
            print("nothing to review")
            return 3
        skip.clear()
        time.sleep(min(15, max(1, deadline - time.time())))


def cmd_post(a, root):
    who = B.author_of(a)
    if not a.cite.strip():
        sys.exit("review: --cite is required (the command you ran or the file:line you read)")
    items, _, _, _ = _state(_msgs(root))
    item = items.get(a.on)
    if item is None:
        sys.exit(f"review: #{a.on} is not a reviewable plan/result")
    if item.get("from") == who:
        sys.exit("review: you cannot review your own work")
    d = _post(root, who, "review", a.text, thread="review", reply_to=a.on, verdict=a.verdict, cite=a.cite)
    (_reserve_dir(root) / f"{a.on}-{who}.lock").unlink(missing_ok=True)
    print(f"review #{d['seq']} on #{a.on}: {a.verdict}")
    return 0


def cmd_reply(a, root):
    who = B.author_of(a)
    _, _, challenges, _ = _state(_msgs(root))
    if a.to not in challenges:
        sys.exit(f"review: #{a.to} is not an open challenge")
    d = _post(root, who, "review", a.text, thread="review", reply_to=a.to, verdict="answer")
    print(f"answer #{d['seq']} to challenge #{a.to}")
    return 0


def cmd_resolve(a, root):
    who = B.author_of(a)
    items, _, challenges, _ = _state(_msgs(root))
    c = challenges.get(a.challenge)
    if c is None:
        sys.exit(f"review: #{a.challenge} is not an open challenge")
    item_author = items.get(c["reply_to"], {}).get("from")
    if who != c.get("from") and not (who == LEAD and item_author != LEAD):
        sys.exit(f"review: only the challenger ({c.get('from')}) may resolve #{a.challenge}"
                 + ("" if item_author == LEAD else f", or {LEAD} by overrule"))
    if not a.text.strip():
        sys.exit("review: say why it is settled")
    over = who != c.get("from")
    d = _post(root, who, "review", a.text, thread="review", reply_to=a.challenge, verdict="resolve",
              overrule="yes" if over else None)
    print(f"resolved #{a.challenge} (#{d['seq']})" + (" -- LEAD OVERRULE: check() reds it until accepted" if over else ""))
    return 0


def cmd_submit(a, root):
    who = B.author_of(a)
    d = _post(root, who, "plan", a.text, thread=a.thread)
    print(f"plan #{d['seq']} posted for review")
    return 0


def cmd_open(a, root):
    items, _, challenges, overruled = _state(_msgs(root))
    for c in challenges.values():
        item = items.get(c["reply_to"], {})
        print(f"OPEN #{c['seq']} {c['from']} challenges #{c['reply_to']} by {item.get('from')}: {c.get('body', '')[:160]}")
    for s, c in overruled.items():
        print(f"OVERRULED #{s} ({c['from']} on #{c['reply_to']}) -- needs `check --accept-overrule {s}`")
    print(f"{len(challenges)} open challenge(s), {len(overruled)} overruled")
    return 0


def cmd_check(a, root):
    if a.min < 1:
        sys.exit("review: --min must be at least 1")
    msgs = _msgs(root)
    rec = next((m for m in msgs if m.get("kind") == "task-open" and m.get("thread") == a.task), None)
    reds, n = [], 0
    if rec is None:
        reds.append(f"no task-open for {a.task} on this board")
    start = rec["seq"] if rec else 0
    items, reviews_of, challenges, overruled = _state(msgs)
    open_on, over_on = {}, {}
    for c in challenges.values():
        open_on.setdefault(c["reply_to"], []).append(c["seq"])
    for s, c in overruled.items():
        if s not in (a.accept_overrule or []):
            over_on.setdefault(c["reply_to"], []).append(s)
    for seq, item in sorted(items.items()):
        # the task's thread plus untagged messages, the same window score.py scores
        if seq <= start or item.get("thread") not in (a.task, None):
            continue
        n += 1
        agree = {r.get("from") for r in reviews_of[seq]
                 if r.get("verdict") == "agree" and r.get("from") != item.get("from") and r.get("cite")}
        if len(agree) < a.min:
            reds.append(f"#{seq} {item['kind']} by {item.get('from')}: {len(agree)} independent agree review(s) < {a.min}")
        if seq in open_on:
            reds.append(f"#{seq}: open challenge(s) {open_on[seq]}")
        if seq in over_on:
            reds.append(f"#{seq}: challenge(s) {over_on[seq]} closed by lead overrule, not accepted")
    if n == 0 and rec is not None:
        reds.append(f"no plan/result in thread {a.task} after its task-open -- nothing was reviewed")
    print(f"{a.task}: reviewed={0 if reds else 1}  items={n}  min={a.min}")
    for r in reds:
        print(f"  RED reviewed: {r}")
    return 1 if reds else 0


def _take_dir(root):
    d = root / "takes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _taker(root, seq):
    """Who holds a LIVE take on request seq (None: untaken, or the take's lease expired and it is open again)."""
    f = root / "takes" / str(seq)
    lease = B.read_lease(f) if f.exists() else None
    return lease["who"] if lease and not B.expired(lease) else None


def _may_take(m, who):
    return m.get("from") != who and m.get("to") in (None, "all", who)


def cmd_requests(a, root):
    who = getattr(a, "as_", None)
    msgs = _msgs(root)
    by = {m["seq"]: m for m in msgs}
    retracted = {m.get("reply_to") for m in msgs if m.get("kind") == "retract"
                 and by.get(m.get("reply_to"), {}).get("from") == m.get("from")}
    open_ = [m for m in msgs if m.get("kind") == "request" and m["seq"] not in retracted
             and _taker(root, m["seq"]) is None and (not who or _may_take(m, who))]
    for m in open_:
        to = f">{m['to']}" if m.get("to") else ""
        print(f"#{m['seq']} {m.get('from')}{to} [{m.get('thread') or '-'}]: {' '.join(str(m.get('body', '')).split())[:140]}")
    print(f"{len(open_)} open request(s)" + (f" {who} may take" if who else ""))
    return 0


def cmd_take(a, root):
    # A take is a lease (default 4h, like claims): the lane hook and `fleet.py renew-live` renew a live taker's take;
    # a dead taker's take expires, the request reopens, and the next taker's takeover is posted on the record.
    who = B.author_of(a)
    m = {m["seq"]: m for m in _msgs(root)}.get(a.n)
    if not m or m.get("kind") != "request":
        sys.exit(f"review: #{a.n} is not a request")
    if m.get("from") == who:
        sys.exit(f"review: #{a.n} is your own request")
    if not _may_take(m, who):
        sys.exit(f"review: #{a.n} is addressed to {m.get('to')}")
    ttl = B.parse_ttl(a.ttl) if a.ttl is not None else B.DEFAULT_TTL
    f = _take_dir(root) / str(a.n)
    for attempt in (1, 2):
        try:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            break
        except FileExistsError:
            old = B.read_lease(f)
            if attempt == 2 or not (old and B.expired(old)):
                print(f"review: #{a.n} already taken by {old['who'] if old else '?'}", file=sys.stderr)
                return 1
            grave = f.with_name(f"{f.name}.expired-{os.getpid()}-{time.time_ns()}")
            try:
                os.replace(f, grave)   # one re-taker wins the move; the loser sees the fresh take on attempt 2
            except FileNotFoundError:
                continue
            B.post(root, who, "status", f"re-taking #{a.n}: the take of {old['who']} expired", thread=m.get("thread"),
                   reply_to=a.n)
            grave.unlink(missing_ok=True)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(B.lease_text(who, ttl, "take"))
    d = B.post(root, who, "status", f"taking #{a.n}: {' '.join(str(m.get('body', '')).split())[:100]}",
               thread=m.get("thread"), reply_to=a.n)
    print(f"took #{a.n} (#{d['seq']})")
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # board text holds chars a cp1252 pipe cannot encode (a BOM, the stop sign)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog="review", description=__doc__.splitlines()[0])
    ap.add_argument("--board"); ap.add_argument("--project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit"); s.add_argument("--as", dest="as_"); s.add_argument("--thread", required=True); s.add_argument("text")
    n = sub.add_parser("next"); n.add_argument("--as", dest="as_"); n.add_argument("--min", type=int, default=2); n.add_argument("--wait", type=int, default=0)
    p = sub.add_parser("post"); p.add_argument("--as", dest="as_"); p.add_argument("--on", type=int, required=True)
    p.add_argument("--verdict", choices=("agree", "challenge"), required=True); p.add_argument("--cite", required=True); p.add_argument("text")
    r = sub.add_parser("reply"); r.add_argument("--as", dest="as_"); r.add_argument("--to", type=int, required=True); r.add_argument("text")
    v = sub.add_parser("resolve"); v.add_argument("--as", dest="as_"); v.add_argument("--challenge", type=int, required=True); v.add_argument("text")
    sub.add_parser("open")
    c = sub.add_parser("check"); c.add_argument("task"); c.add_argument("--min", type=int, default=2)
    c.add_argument("--accept-overrule", type=int, nargs="*", help="lead overrules the lead has read and accepts")
    sub.add_parser("requests").add_argument("--as", dest="as_")
    t = sub.add_parser("take"); t.add_argument("n", type=int); t.add_argument("--as", dest="as_")
    t.add_argument("--ttl", help="lease length: 30m, 4h, 2d (default 4h); a live taker's lane renews it")
    a = ap.parse_args(argv)
    root = B.board_root(a)
    return {"submit": cmd_submit, "next": cmd_next, "post": cmd_post, "reply": cmd_reply, "resolve": cmd_resolve,
            "open": cmd_open, "check": cmd_check, "requests": cmd_requests, "take": cmd_take}[a.cmd](a, root)


if __name__ == "__main__":
    sys.exit(main())
