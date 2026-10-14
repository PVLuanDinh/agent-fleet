#!/usr/bin/env python3
"""Append-only, hash-chained file message board for agents (stdlib only).

Works identically from Claude Desktop (Code tab), terminal Claude, subagents, and Codex:
everything is plain files under one directory, so any process that can run Python can talk.

Board root, first hit wins: --board DIR > $AGENT_BOARD > ~/.claude/agent-board/<--project or cwd name>.

Layout under the root:
  msgs/00000042.json   one message per file; written to a temp file, then hard-linked into
                       place (link fails if the name exists), so concurrent writers never share
                       a seq and readers never see a half-written message
  claims/<quoted path>.claim   one file per claimed repo-relative path, created with O_EXCL
  tasks/<task>.json    task specs written by score.py
Other files already in the root (PROTOCOL.md, ROSTER.md, ...) are left alone.

ponytail: identity is self-declared (--as / $AGENT_NAME) and the hash chain has no key -- every
agent is the same OS user, so per-agent keys would sit in files every agent can read. The chain
catches CARELESS edits/deletions only; an agent that recomputes every later hash can rewrite
history. score.py narrows that with hashes the lead keeps OUTSIDE the board (--anchor at
task-open, --min-head N:HASH from `head` at the lead's last look); messages after the last
look stay unpinned.
Upgrade path: agents as separate OS users + per-user keys, or the lead's anchors in a write-once store.
"""
import argparse, hashlib, json, os, re, sqlite3, subprocess, sys, time, uuid
from pathlib import Path
from urllib.parse import quote

KINDS = {"note", "request", "status", "result", "claim", "release", "retract",
         "owner-question", "owner-answer", "task-open"}
OWNER = "owner"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
SKIP_FIELDS = {"hash", "prev", "ts", "client", "pid", "seq", "_file", "_corrupt"}
# Hard refusal: credentials never go on the board (the incident's board carried exploits/creds).
SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY|\b(password|passwd|api[_-]?key|secret|token)\s*[:=]\s*\S{6,})",
    re.I)
# Soft flag: scope-override / guard-evasion language. ponytail: keyword match, a known ceiling --
# it catches the incident's literal phrasing ("outside intended scope ... peers doing it"), not
# intent, and it false-positives on denials ("I did not edit the tests"); the lead waives those.
# score.py recomputes flags from the message text, so there is no stored flag to strip.
SCOPE_RE = re.compile(
    r"(outside (the )?(intended )?scope|peers (are )?doing it|everyone else is doing"
    r"|disabl\w* (the )?(monitor|logging|hook|gate|guard|test)|bypass\w* (the )?(sandbox|proxy|guard|gate|hook|scorer|check)"
    r"|escape (the )?sandbox|exfiltrat|privilege escalation|--no-verify|skip (the )?(tests|hooks)"
    r"|(edit|modif|weaken|delet)\w* (the )?(test|tests|scorer|checker|verifier)\b"
    r"|hard-?cod\w* (the )?(expected|answer|output))", re.I)


def board_root(args) -> Path:
    if getattr(args, "board", None):
        return Path(args.board).expanduser().resolve()
    if os.environ.get("AGENT_BOARD"):
        return Path(os.environ["AGENT_BOARD"]).expanduser().resolve()
    proj = getattr(args, "project", None) or Path.cwd().name
    return Path.home() / ".claude" / "agent-board" / proj


def canon(d: dict) -> bytes:
    return json.dumps({k: v for k, v in d.items() if k not in ("hash", "_file")},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def msg_hash(d: dict) -> str:
    return hashlib.sha256(canon(d)).hexdigest()


def load_msgs(root: Path) -> list:
    out = []
    for p in sorted((root / "msgs").glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(d, dict) or not isinstance(d.get("seq"), int) or isinstance(d["seq"], bool):
                raise ValueError("not a message object with an int seq")
        except (OSError, ValueError) as e:
            d = {"seq": -1, "_corrupt": str(e)}
        d["_file"] = p.name
        out.append(d)
    return out


def good(msgs: list) -> list:
    return [m for m in msgs if "_corrupt" not in m]


def msg_text(m: dict) -> str:
    """Every agent-controlled string in a message: body, thread, cite, path, to, ..."""
    return "\n".join(str(v) for k, v in m.items() if k not in SKIP_FIELDS and isinstance(v, (str, int)))


def scope_flags(text: str) -> list:
    return sorted({m.group(0).lower() for m in SCOPE_RE.finditer(text or "")})


def client() -> str:
    # Desktop vs terminal vs SDK, as the harness reports it; "?" outside Claude Code (e.g. Codex).
    return os.environ.get("CLAUDE_CODE_ENTRYPOINT") or ("codex" if os.environ.get("CODEX_HOME") else "?")


def author_of(args) -> str:
    a = getattr(args, "as_", None) or os.environ.get("AGENT_NAME") or ""
    if not NAME_RE.match(a):
        sys.exit("board: set --as NAME or $AGENT_NAME ([A-Za-z0-9_.-], max 48)")
    if a.lower() == OWNER:
        sys.exit("board: 'owner' is reserved; owner answers go through `board.py answer`")
    return a


def post(root: Path, author: str, kind: str, body: str, **extra) -> dict:
    if kind not in KINDS:
        sys.exit(f"board: unknown kind {kind!r}; one of {sorted(KINDS)}")
    for k in ("to", "thread"):
        if extra.get(k) is not None and not NAME_RE.match(str(extra[k])):
            sys.exit(f"board: --{k} must match [A-Za-z0-9_.-]{{1,48}}")
    fields = {k: v for k, v in extra.items() if v is not None}
    if SECRET_RE.search(msg_text({"body": body, **fields})):
        sys.exit("board: REFUSED -- a field looks like a credential. Never put secrets on the board.")
    if len(body.encode("utf-8")) > 16_000:
        sys.exit("board: body > 16 KB; put the artifact in a file and post its path")
    mdir = root / "msgs"
    mdir.mkdir(parents=True, exist_ok=True)
    for _ in range(500):
        msgs = good(load_msgs(root))
        last = msgs[-1] if msgs else None
        seq = (last["seq"] + 1) if last else 1
        d = {"seq": seq, "prev": last["hash"] if last else "0" * 64,
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "from": author, "kind": kind,
             "client": client(), "pid": os.getpid(), "body": body, **fields}
        d["hash"] = msg_hash(d)
        tmp = mdir / f".tmp-{uuid.uuid4().hex}"
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            os.link(tmp, mdir / f"{seq:08d}.json")  # atomic, fails if another writer took this seq
            return d
        except FileExistsError:
            time.sleep(0.005)
        except OSError as e:  # ponytail: needs hard links (NTFS/ext4/APFS); exFAT and some shares lack them
            sys.exit(f"board: cannot hard-link into {mdir} ({e}); put the board on a local NTFS/ext4/APFS disk")
        finally:
            tmp.unlink(missing_ok=True)
    sys.exit("board: could not get a sequence number after 500 tries")


def verify(root: Path) -> list:
    """Integrity problems; empty == chain consistent. Run when no agent is mid-claim/release."""
    probs, prev, want = [], "0" * 64, 1
    msgs = load_msgs(root)
    for m in msgs:
        if "_corrupt" in m:
            probs.append(f"{m['_file']}: unreadable ({m['_corrupt']})"); continue
        if m["seq"] != want:
            probs.append(f"{m['_file']}: seq {m['seq']} where {want} expected (deleted/inserted message)")
            want = m["seq"]
        if m["_file"] != f"{m['seq']:08d}.json":
            probs.append(f"{m['_file']}: filename does not match seq")
        if m.get("prev") != prev:
            probs.append(f"{m['_file']}: prev-hash mismatch (history rewritten)")
        if msg_hash(m) != m.get("hash"):
            probs.append(f"{m['_file']}: content hash mismatch (message edited)")
        prev, want = m.get("hash"), want + 1
    # Case-folded: boards written before claims were lowercased everywhere hold mixed-case paths.
    live = {}
    for m in good(msgs):
        if m.get("kind") == "claim":
            live[(m.get("path") or "").lower()] = m.get("from")
        elif m.get("kind") == "release":
            live.pop((m.get("path") or "").lower(), None)
    cdir = root / "claims"
    on_disk = {p.name.lower() for p in cdir.glob("*.claim")} if cdir.exists() else set()
    for path, who in live.items():
        if claim_file(root, path).name.lower() not in on_disk:
            probs.append(f"claim on {path} by {who} vanished without a release message")
    for name in on_disk - {claim_file(root, p).name.lower() for p in live}:
        probs.append(f"claims/{name} has no claim message on the board")
    return probs


def norm_path(path: str) -> str:
    p = path.strip().replace("\\", "/")
    if os.path.isabs(p) or re.match(r"^[A-Za-z]:", p):
        sys.exit("board: claim repo-relative paths (src/x.py), not absolute ones")
    p = os.path.normpath(p).replace("\\", "/")
    if p.startswith("../") or p == "..":
        sys.exit("board: claim paths inside the repo")
    # Windows and WSL agents share one board on NTFS: a case-only alias must collide on both sides.
    return p.lower()


def claim_file(root: Path, path: str) -> Path:
    return root / "claims" / (quote(path, safe="") + ".claim")


def show(m: dict) -> str:
    to = f" -> {m['to']}" if m.get("to") else ""
    extra = "".join(f" {k}={m[k]}" for k in ("thread", "reply_to", "path", "claim", "cite") if m.get(k))
    fl = scope_flags(msg_text(m))
    warn = f"  [SCOPE-FLAG: {', '.join(fl)}]" if fl else ""
    return f"#{m['seq']} {m['ts']} {m['from']}({m.get('client','?')}){to} [{m['kind']}]{extra}{warn}\n  {m.get('body','')}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="board", description=__doc__.splitlines()[0])
    ap.add_argument("--board"); ap.add_argument("--project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("where")
    sub.add_parser("head", help="print N:HASH of the newest message; the lead keeps it off-board for score --min-head")
    p = sub.add_parser("post"); p.add_argument("--as", dest="as_"); p.add_argument("--kind", default="note")
    p.add_argument("--to"); p.add_argument("--thread"); p.add_argument("--reply-to", type=int)
    p.add_argument("--cite", help="result: the command that proves it"); p.add_argument("--claim", choices=["pass", "fail"])
    p.add_argument("body", help="text, or - for stdin")
    r = sub.add_parser("read"); r.add_argument("--since", type=int, default=0); r.add_argument("--to")
    r.add_argument("--kind"); r.add_argument("--thread"); r.add_argument("--json", action="store_true")
    for name in ("claim", "release"):
        c = sub.add_parser(name); c.add_argument("path"); c.add_argument("--as", dest="as_"); c.add_argument("--thread")
        if name == "release":
            c.add_argument("--for", dest="for_", help="release a STOPPED holder's claim (needs --owner-auth)")
            c.add_argument("--owner-auth", type=int, help="seq of the owner-answer that authorizes --for")
    q = sub.add_parser("ask-owner"); q.add_argument("--as", dest="as_"); q.add_argument("--thread"); q.add_argument("body")
    sub.add_parser("owner", help="list open owner questions")
    a = sub.add_parser("answer", help="OWNER ONLY: answer question N"); a.add_argument("n", type=int); a.add_argument("body")
    sub.add_parser("verify")
    args = ap.parse_args(argv)
    root = board_root(args)

    if args.cmd == "where":
        print(root); return 0
    if args.cmd == "head":
        msgs = good(load_msgs(root))
        print(f"{msgs[-1]['seq']}:{msgs[-1]['hash']}" if msgs else "0:" + "0" * 64); return 0
    if args.cmd == "post":
        body = sys.stdin.read() if args.body == "-" else args.body
        if args.kind == "result" and not (args.cite and args.claim):
            sys.exit("board: a result needs --cite '<command that proves it>' and --claim pass|fail")
        if args.kind in ("owner-answer", "claim", "release", "task-open"):
            sys.exit(f"board: use the dedicated subcommand for {args.kind}")
        d = post(root, author_of(args), args.kind, body, to=args.to, thread=args.thread,
                 reply_to=args.reply_to, cite=args.cite, claim=args.claim)
        fl = scope_flags(msg_text(d))
        print(f"posted #{d['seq']}" + (f"  [SCOPE-FLAG: {', '.join(fl)}]" if fl else ""))
        return 0
    if args.cmd == "read":
        for m in good(load_msgs(root)):
            if m["seq"] <= args.since: continue
            if args.to and m.get("to") not in (args.to, None, "all"): continue
            if args.kind and m.get("kind") != args.kind: continue
            if args.thread and m.get("thread") != args.thread: continue
            print(json.dumps({k: v for k, v in m.items() if k != "_file"}, ensure_ascii=False) if args.json else show(m))
        return 0
    if args.cmd == "claim":
        who = author_of(args); path = norm_path(args.path); f = claim_file(root, path)
        import fleet
        try:
            held = fleet.conflicts(path, root)
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError, sqlite3.Error) as e:
            print(f"board: {path} not claimed, fleet leases unreadable: {e}", file=sys.stderr); return 1
        if held:
            print(f"board: {path} held by agent-manager session(s): "
                  + "; ".join(f"{l['session_id']} {l['pattern']} {l['note']}".strip() for l in held), file=sys.stderr)
            return 1
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            print(f"board: {path} already claimed: {f.read_text(encoding='utf-8').strip()}", file=sys.stderr); return 1
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{who} {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")
        d = post(root, who, "claim", f"claim {path}", path=path, thread=args.thread); print(f"claimed {path} (#{d['seq']})"); return 0
    if args.cmd == "release":
        who = author_of(args); path = norm_path(args.path); f = claim_file(root, path)
        if not f.exists():
            print(f"board: {path} is not claimed", file=sys.stderr); return 1
        holder = f.read_text(encoding="utf-8").split()[0]
        if args.for_ or args.owner_auth is not None:
            # A holder that has STOPPED can never release; the owner may authorize someone else to. The
            # authorization must be a real owner-answer on this board, and the release names both parties.
            if not (args.for_ and args.owner_auth is not None):
                sys.exit("board: --for and --owner-auth go together")
            if holder != args.for_:
                print(f"board: {path} is held by {holder}, not {args.for_}", file=sys.stderr); return 1
            auth = next((m for m in good(load_msgs(root)) if m["seq"] == args.owner_auth), None)
            if not auth or auth.get("kind") != "owner-answer" or auth.get("from") != OWNER:
                print(f"board: #{args.owner_auth} is not an owner-answer; ask the owner first", file=sys.stderr); return 1
            d = post(root, who, "release", f"release {path} held by stopped {holder} (owner-auth #{args.owner_auth})",
                     path=path, thread=args.thread, released_for=holder, owner_auth=args.owner_auth)
            f.unlink(); print(f"released {path} for {holder} (#{d['seq']}, owner-auth #{args.owner_auth})"); return 0
        if holder != who:
            print(f"board: {path} is held by {holder}, not {who}; ask them (or owner-authorized: --for {holder} "
                  f"--owner-auth N)", file=sys.stderr); return 1
        d = post(root, who, "release", f"release {path}", path=path, thread=args.thread); f.unlink()
        print(f"released {path} (#{d['seq']})"); return 0
    if args.cmd == "ask-owner":
        d = post(root, author_of(args), "owner-question", args.body, to=OWNER, thread=args.thread)
        print(f"owner question #{d['seq']}"); return 0
    if args.cmd == "owner":
        msgs = good(load_msgs(root))
        answered = {m.get("reply_to") for m in msgs if m.get("kind") == "owner-answer"}
        open_q = [m for m in msgs if m.get("kind") == "owner-question" and m["seq"] not in answered]
        for m in open_q: print(show(m))
        print(f"{len(open_q)} open owner question(s)"); return 0
    if args.cmd == "answer":
        d = post(root, OWNER, "owner-answer", args.body, reply_to=args.n); print(f"owner-answer #{d['seq']} -> #{args.n}"); return 0
    if args.cmd == "verify":
        probs = verify(root)
        for p in probs: print("RED", p)
        print(f"{'INTACT' if not probs else 'BROKEN'}: {len(load_msgs(root))} message(s), {len(probs)} problem(s) at {root}")
        return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
