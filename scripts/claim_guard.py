#!/usr/bin/env python3
"""Git pre-commit guard: refuse a commit that touches a file someone ELSE holds a board claim on.

Board claims were checked only when an agent asked for one; a lane that skipped the claim, or committed after
losing it, could still overwrite a held file. This enforces claims at the point of damage (the commit), the way
mcp_agent_mail's pre-commit guard enforces its file leases. It only READS the board (board.py/score.py untouched).

  claim_guard.py install --repo DIR --board ROOT --as NAME   write the pre-commit hook (chains an existing one)
  claim_guard.py check --repo DIR --board ROOT [--as NAME]   what the hook runs; exit 1 lists each held path
Identity: --as, else $AGENT_NAME / $AGENT_FLEET_AS, else <git-dir>/agent-fleet-as (written by install, per worktree).
No identity = a human or the integrator committing: allowed with a note. Claimed paths match case-folded, as
board.py claims do. With the fleet layer on (fleet.on), an exclusive agent-fleet lease counts as a claim too.
Bypass is git's own `--no-verify` (only on the owner's word).
"""
import argparse, os, re, stat, subprocess, sys
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
HOOK_MARK = "# agent-fleet claim guard"


def native(p: str) -> str:
    """A board path given in Windows form still works from WSL/Linux, and vice versa for /mnt paths on Windows."""
    if os.name != "nt":
        return re.sub(r"^([A-Za-z]):[\\/]", lambda m: f"/mnt/{m.group(1).lower()}/", p).replace("\\", "/")
    m = re.match(r"^/mnt/([a-z])/(.*)$", p)
    return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\") if m else p


def git(repo, *a) -> str:
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()


def git_dir(repo) -> Path:
    return Path(git(repo, "rev-parse", "--absolute-git-dir"))


def identity(repo, given) -> str:
    if given:
        return given
    for v in ("AGENT_NAME", "AGENT_FLEET_AS"):
        if os.environ.get(v):
            return os.environ[v]
    f = git_dir(repo) / "agent-fleet-as"
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


def norm(p: str) -> str:
    return os.path.normpath(p.strip().replace("\\", "/")).replace("\\", "/").lower()


def check(repo, board, who) -> list:
    staged = git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMRD").splitlines()
    claims = Path(native(board)) / "claims"
    held = []
    for rel in staged:
        p = norm(rel)
        f = claims / (quote(p, safe="") + ".claim")
        if f.exists():
            sys.path.insert(0, str(HERE))
            import board as board_mod   # not `board`: that name is this function's board-path argument
            lease = board_mod.read_lease(f)
            # only a LIVE EXCLUSIVE claim by someone else blocks; shared means others may edit, expired means free
            if lease and lease["who"] != who and lease["mode"] != "shared" and not board_mod.expired(lease):
                held.append(f"{rel}: claimed by {lease['who']} ({board_mod.until(lease)})")
        if (Path(native(board)) / "fleet.on").exists():
            sys.path.insert(0, str(HERE))
            import fleet
            for l in fleet.conflicts(p, Path(native(board))):
                held.append(f"{rel}: agent-fleet session {l['session_id']} holds an exclusive lease ({l['pattern']})")
    return held


def install(repo, board, who) -> str:
    hooks = Path(git(repo, "rev-parse", "--git-path", "hooks"))
    hooks = hooks if hooks.is_absolute() else Path(repo) / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    prev = hooks / "pre-commit.agent-fleet-prev"
    if hook.exists() and HOOK_MARK not in hook.read_text(encoding="utf-8", errors="replace"):
        hook.replace(prev)   # keep the repo's own gate; the guard runs it first
    me = str(Path(__file__).resolve()).replace("\\", "/")
    body = f"""#!/bin/sh
{HOOK_MARK} (installed by claim_guard.py; the repo's previous hook, if any, runs first)
d="$(dirname "$0")"
if [ -x "$d/pre-commit.agent-fleet-prev" ]; then "$d/pre-commit.agent-fleet-prev" "$@" || exit $?; fi
[ -f "{me}" ] || exit 0   # guard script moved/removed: never block every commit in a shared hooks dir
PY="{Path(sys.executable).as_posix()}"   # the interpreter that installed the guard (python3 can be a Store stub)
[ -x "$PY" ] || PY=python3; command -v "$PY" >/dev/null 2>&1 || PY=python
"$PY" "{me}" check --repo "$(git rev-parse --show-toplevel)" --board "{board.replace(chr(92), '/')}"
"""
    hook.write_text(body, encoding="utf-8", newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if who:
        (git_dir(repo) / "agent-fleet-as").write_text(who + "\n", encoding="utf-8")
    return str(hook)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="claim_guard", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("install", "check"):
        s = sub.add_parser(name); s.add_argument("--repo", default="."); s.add_argument("--board", required=True)
        s.add_argument("--as", dest="as_", required=(name == "install"))
    a = ap.parse_args(argv)
    if a.cmd == "install":
        print(f"claim guard installed: {install(a.repo, a.board, a.as_)} (commits as {a.as_})"); return 0
    who = identity(a.repo, a.as_)
    if not who:
        print("claim guard: no agent identity (human/integrator commit) -- claims not enforced", file=sys.stderr)
        return 0
    try:
        held = check(a.repo, a.board, who)
    except Exception as e:   # an unreadable board must not silently pass a lane's commit
        print(f"claim guard: cannot check claims ({e}); commit refused (git commit --no-verify only on the owner's word)",
              file=sys.stderr)
        return 1
    if held:
        print(f"claim guard: commit refused for {who}; these staged files are held by someone else:\n  "
              + "\n  ".join(held) + "\nUnstage them, or ask the holder on the board (board.py post --to <holder>).",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
