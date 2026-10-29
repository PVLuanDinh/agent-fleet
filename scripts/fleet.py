#!/usr/bin/env python3
"""Bridge from the board to the agent-fleet binary, the tmux agent fleet (branch tui of the agent-fleet repo).

agent-fleet is Linux/macOS only, so on Windows every call goes through `wsl.exe -d <distro>`.

  fleet.py am <args...>        run agent-fleet (TUI with no args: needs a real terminal)
  fleet.py leases [--json]     live agent-fleet file leases (read-only, from its state.db)
  fleet.py mcp-config          the stdio MCP command that lets a Desktop/CLI session drive the fleet
  fleet.py --board DIR on|off  (or --project NAME) join or leave one claim domain: with `on`, board.py claim also
                               refuses a path an agent-fleet session holds an exclusive lease on
  fleet.py trust DIR...        pre-accept Claude Code's "trust this folder" prompt for DIR inside WSL
  fleet.py spawn --directory DIR [agent-fleet spawn args]   trust DIR, then spawn (Windows paths ok)
                               Admission (the project's max_active_lanes and min_available_mb in
                               fleet-requirements.json): over either, exit 3 and nothing spawns;
                               --wait-admission SECONDS queues it, --no-admission overrides
  fleet.py renew-live [--every N]   renew the leases (claims, takes) of roster lanes whose session is alive; the hidden
                               TUI runs it every 300 s, so a long tool call inside a lane cannot lapse its leases
  fleet.py resurrect [NAME... | --all]   list roster lanes with no live session (a crash or reboot cut off the
                               ones with no clean end); respawn named ones, or --all cut-off ones
  fleet.py sessions [--json]   agent-fleet sessions, each checked for a blocking prompt or a vanished pane
  fleet.py kill ID...          stop a session from outside agent-fleet (its own `kill` needs a caller)
  fleet.py ui start|stop|status|shot [N]|keys K...|attach   one hidden agent-fleet TUI (tmux socket fleetui)
                               that keeps session status current; any session can screenshot or drive it
  fleet.py archive-dead        archive every dead row (agent-fleet archive-dead; kill and spawn also run it)
  fleet.py preflight [--cwd DIR] [--no-fix]   install + functionally check every skill the project's lanes need
                               (~/.claude/fleet-requirements.json); `spawn` runs it first and refuses on a required failure

Leak guard (leakguard.py, Windows host): spawn, kill and archive-dead log the kernel nonpaged pool and pool tag NtFC
(the ntfs.sys leak) before and after, plus a follow-up ~45 s later, to ~/.claude/agent-fleet-leak.log; spawn is
refused while the nonpaged pool is >= FLEET_NONPAGED_MAX_MB (8192). FLEET_LEAKGUARD_OFF=1 overrides the refusal.

Env: AGENT_FLEET_WSL_DISTRO (default Ubuntu); AGENT_FLEET_AM_DB reads that state.db file
directly (tests, or a non-default agent-fleet config dir).

ponytail: leases are matched on the path text only, not on which checkout the agent-fleet
session runs in, so two repos with the same relative path collide. A false refusal, never a
missed one. Upgrade path: compare against the session's repo column in state.db.
"""
import argparse, fnmatch, hashlib, json, os, re, shutil, sqlite3, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import roster  # noqa: E402
DISTRO = os.environ.get("AGENT_FLEET_WSL_DISTRO", "Ubuntu")
MARKER = "fleet.on"
LEASE_SQL = ("SELECT pattern, mode, session_id, note, expires_at FROM file_reservations "
             "WHERE expires_at > ? ORDER BY acquired_at")
# Runs inside WSL; prints NODB when agent-fleet has never run there, so "no fleet" is not an error.
WSL_LEASES = (
    "import json,os,sqlite3,sys,time\n"
    "d=os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')\n"
    "p=os.path.join(d,'agent-fleet','state.db')\n"
    "if not os.path.exists(p): print('NODB'); sys.exit(0)\n"
    "c=sqlite3.connect('file:'+p+'?mode=ro',uri=True)\n"
    "print(json.dumps(c.execute(sys.argv[1],(time.time_ns(),)).fetchall()))\n")


# The fleet binary: agent-fleet, built from this repo's tui branch (lives in ~/.local/bin).
AM = "agent-fleet"
TMUX_SOCKET = "agentfleet"  # agent-fleet's own tmux server; each session is tmux session am_<id>
# Runs inside WSL: mark folders trusted in Claude Code's ~/.claude.json, written atomically.
WSL_TRUST = (
    "import json,os,sys,tempfile\n"
    "p=os.path.expanduser('~/.claude.json')\n"
    "d=json.load(open(p,encoding='utf-8')) if os.path.exists(p) else {}\n"
    "for f in sys.argv[1:]:\n"
    " e=d.setdefault('projects',{}).setdefault(f,{}); e['hasTrustDialogAccepted']=True\n"
    # The CLAUDE.md external-imports prompt parks a lane too; answer it as its own default ("No"), never wider.
    " e['hasClaudeMdExternalIncludesWarningShown']=True; e.setdefault('hasClaudeMdExternalIncludesApproved',False)\n"
    "fd,t=tempfile.mkstemp(dir=os.path.dirname(p))\n"
    "with os.fdopen(fd,'w',encoding='utf-8') as h: json.dump(d,h,indent=2)\n"
    "os.chmod(t,0o600); os.replace(t,p); print('trusted',*sys.argv[1:])\n")
# A pane parked on one of these never starts its task, and agent-fleet still shows it as "starting".
BLOCKERS = [("trust prompt", r"trust (the files in )?this folder"), ("login", r"select login method|please run /login"),
            ("theme picker", r"choose the text style"), ("external imports prompt", r"allow external imports"),
            ("read-outside prompt", r"allow this read outside"),
            ("context limit", r"prompt is too long|context limit reached|conversation is too long"),
("permission prompt", r"do you want to (proceed|allow)")]


def wsl_prefix() -> list:
    # --exec, not --: `wsl.exe --` re-joins argv into one shell string and loses the quoting.
    return [] if os.name != "nt" else ["wsl.exe", "-d", DISTRO, "--exec"]


def to_wsl_path(p: str) -> str:
    """C:\\Users\\x -> /mnt/c/Users/x; Linux paths pass through."""
    m = re.match(r"^([A-Za-z]):[\\/]*(.*)$", p.strip())
    if not m:
        return p.strip().rstrip("/") or "/"
    rest = m.group(2).replace("\\", "/").rstrip("/")
    return f"/mnt/{m.group(1).lower()}" + (f"/{rest}" if rest else "")


def blocked_reason(screen: str) -> str:
    low = screen.lower()
    return next((name for name, rx in BLOCKERS if re.search(rx, low)), "")


def in_wsl(args: list, **kw) -> subprocess.CompletedProcess:
    # utf-8 explicitly: Windows' default (cp1252) dies on the box-drawing characters agent screens are made of.
    return subprocess.run(wsl_prefix() + args, capture_output=True, encoding="utf-8", errors="replace", timeout=60, **kw)


def trust(dirs: list) -> int:
    paths = [to_wsl_path(d) for d in dirs]
    p = in_wsl(["python3", "-c", WSL_TRUST] + paths)
    print((p.stdout or p.stderr).strip())
    return p.returncode


UI_SOCKET, UI_SESSION = "fleetui", "ui"
# agent-fleet reconciles session status (dead panes, finished turns) only while its TUI runs.


def ui_tmux(*a) -> subprocess.CompletedProcess:
    return in_wsl(["tmux", "-L", UI_SOCKET, *a])


def ui_running() -> bool:
    return ui_tmux("has-session", "-t", UI_SESSION).returncode == 0


def ui_board() -> str:
    """The board the TUI's header strip reads: $AGENT_FLEET_BOARD, else the first project that names one."""
    board = os.environ.get("AGENT_FLEET_BOARD", "")
    if not board:
        try:
            board = next((p["board"] for p in load_requirements().get("projects", []) if p.get("board")), "")
        except RequirementsError:
            board = ""
    return to_wsl_path(board) if board else ""


def ui_start() -> str:
    if ui_running():
        return "running"
    if in_wsl(["bash", "-lc", "command -v agent-fleet"]).returncode != 0:
        return "agent-fleet not installed"
    board = ui_board()
    env = ["-e", f"AGENT_FLEET_BOARD={board}"] if board else []
    ui_tmux("new-session", "-d", "-s", UI_SESSION, "-x", "200", "-y", "50", *env, f"bash -lc 'exec {AM}'")
    time.sleep(3)
    if not ui_running():
        return "failed to start"
    # A second window renews live lanes' leases every 5 minutes, from outside the lanes (fleet.py renew-live).
    me = to_wsl_path(str(HERE / "fleet.py")) if os.name == "nt" else str(HERE / "fleet.py")
    ui_tmux("new-window", "-d", "-t", UI_SESSION, "-n", "renew", f"bash -lc 'exec python3 {me} renew-live --every 300'")
    return "started"


def ui_shot() -> str:
    return ui_tmux("capture-pane", "-p", "-t", UI_SESSION).stdout or ""


def ui_keys(keys: list, pause: float = 0.8) -> None:
    for k in keys:
        ui_tmux("send-keys", "-t", UI_SESSION, k); time.sleep(pause)


def attach_cmd() -> str:
    return " ".join(wsl_prefix() + ["tmux", "-L", UI_SOCKET, "attach", "-t", UI_SESSION])


def dialog_name(screen: str) -> str:
    """The session an open archive dialog names ('archive <name>?'), or ''."""
    m = re.search(r"archive (\S+)\?", screen)
    return m.group(1) if m else ""


def build_core() -> int:
    """Build the Rust board reader (rust/fleetcore) for Windows and, when cargo exists there, for WSL.
    fastboard.py uses bin/fleetcore(.exe) for cold loads and falls back to Python without it."""
    skill = Path(__file__).resolve().parent.parent
    src, out = skill / "rust" / "fleetcore", Path(__file__).resolve().parent / "bin"
    out.mkdir(exist_ok=True)
    target = Path(os.environ.get("CARGO_TARGET_DIR") or Path.home() / ".cache" / "agent-fleet-cargo")
    rc = 0
    if shutil.which("cargo"):
        env = dict(os.environ, CARGO_TARGET_DIR=str(target))
        p = subprocess.run(["cargo", "build", "--release", "-q", "--manifest-path", str(src / "Cargo.toml")], env=env)
        exe = target / "release" / ("fleetcore.exe" if os.name == "nt" else "fleetcore")
        if p.returncode == 0 and exe.exists():
            shutil.copyfile(exe, out / exe.name)
            if os.name != "nt":
                os.chmod(out / exe.name, 0o755)
            print(f"built {out / exe.name}")
        else:
            print("fleet: cargo build failed", file=sys.stderr); rc = 1
    else:
        print("fleet: cargo not found on this side (install rustup); fastboard falls back to Python", file=sys.stderr)
    if os.name == "nt":
        script = ("set -e; command -v cargo >/dev/null || { echo 'no cargo in WSL: the Linux helper is skipped'; exit 0; }; "
                  f"CARGO_TARGET_DIR=$HOME/.cache/agent-fleet-cargo cargo build --release -q --manifest-path '{to_wsl_path(str(src / 'Cargo.toml'))}' && "
                  f"install -m 0755 $HOME/.cache/agent-fleet-cargo/release/fleetcore '{to_wsl_path(str(out / 'fleetcore'))}' && echo built Linux helper")
        p = in_wsl(["bash", "-lc", script])
        print((p.stdout or p.stderr).strip())
    return rc


def archive_dead() -> list:
    """Archive every dead row (status dead, pane gone) through the binary's own archive-dead command.
    No caller and no TUI keystrokes needed; live and finished sessions are never touched."""
    p = in_wsl(["bash", "-lc", f"exec {AM} archive-dead --json"])
    if p.returncode != 0:
        raise RuntimeError(f"fleet: archive-dead failed: {(p.stderr or p.stdout).strip()[:300]}")
    try:
        return [r.get("name", r.get("id", "?")) for r in (json.loads(p.stdout or "[]") or [])]
    except ValueError:
        raise RuntimeError(f"fleet: archive-dead printed no JSON: {p.stdout.strip()[:300]}")


def await_dead(ids: list, timeout: float = 20.0) -> None:
    """Wait (bounded) until killed sessions read dead; the hidden TUI reconciles status within seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = {r.get("id"): r for r in sessions()}
        if all(rows.get(i, {}).get("status") == "dead" for i in ids):
            return
        time.sleep(1.5)


def sessions() -> list:
    p = in_wsl(["bash", "-lc", f"exec {AM} sessions --json"])
    if p.returncode != 0:
        raise RuntimeError(f"fleet: {AM} sessions failed: {p.stderr.strip()[:300]}")
    rows = json.loads(p.stdout or "[]")
    for r in rows:
        r["blocked"] = ""
        if r.get("status") == "dead" or r.get("archived"):
            continue
        cap = in_wsl(["tmux", "-L", TMUX_SOCKET, "capture-pane", "-p", "-t", f"am_{r['id']}"])
        if cap.returncode != 0:
            r["blocked"] = "pane gone (row stale until the TUI next runs)"
        else:
            r["blocked"] = blocked_reason(cap.stdout)
    return rows


def read_db(path: str) -> list:
    c = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        return c.execute(LEASE_SQL, (time.time_ns(),)).fetchall()
    finally:
        c.close()


def leases(strict: bool = False) -> list:
    """Live leases as dicts. Raises RuntimeError when they cannot be read; with strict (the board
    joined the fleet), a missing wsl.exe or agent-fleet state also raises instead of reading as none."""
    if os.environ.get("AGENT_FLEET_AM_DB"):
        rows = read_db(os.environ["AGENT_FLEET_AM_DB"])
    else:
        if os.name == "nt" and not shutil.which("wsl.exe"):
            if strict:
                raise RuntimeError("fleet: board joined the fleet but wsl.exe is not on PATH")
            return []
        # Login shell, like agent-fleet itself, so XDG_CONFIG_HOME from the profile applies.
        p = in_wsl(["bash", "-lc", 'exec python3 -c "$1" "$2"', "fleet", WSL_LEASES, LEASE_SQL])
        out = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
        if p.returncode != 0 or not out:
            raise RuntimeError(f"fleet: cannot read agent-fleet leases (rc={p.returncode}): {p.stderr.strip()[:300]}")
        if out == "NODB":
            if strict:
                raise RuntimeError(f"fleet: board joined the fleet but agent-fleet has no state in WSL distro {DISTRO}")
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
    """Exclusive agent-fleet leases over `path`; empty unless the board joined the fleet."""
    if not (root / MARKER).exists():
        return []
    return [l for l in leases(strict=True) if l["mode"] != "shared" and overlaps(path, l["pattern"])]


PREFLIGHT_TTL = 1800  # seconds a passing preflight is reused, so spawning stays fast


class RequirementsError(Exception):
    pass


def load_requirements() -> dict:
    p = Path(os.environ.get("FLEET_REQUIREMENTS") or Path.home() / ".claude" / "fleet-requirements.json")
    if not p.exists():
        return {}
    try:
        # utf-8-sig: PowerShell writes a BOM. An unreadable file must REFUSE, never read as "no requirements":
        # that silently let a lane spawn past a failing required skill (caught by the live control, 2026-10-16).
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise RequirementsError(f"{p} is not valid JSON ({e})")


def to_win_path(p: str) -> str:
    m = re.match(r"^/mnt/([a-z])(/.*)?$", p)
    return f"{m.group(1).upper()}:" + (m.group(2) or "\\").replace("/", "\\") if m else p


def project_for(cwd: str) -> dict:
    """The requirements entry whose cwd_prefix (one or a list) contains cwd; longest prefix wins."""
    c = to_win_path(str(cwd)).lower().rstrip("\\/")
    best, n = {}, -1
    for proj in load_requirements().get("projects", []):
        prefixes = proj.get("cwd_prefix") or []
        for pre in [prefixes] if isinstance(prefixes, str) else prefixes:
            pl = pre.lower().rstrip("\\/")
            if c.startswith(pl) and len(pl) > n:
                best, n = proj, len(pl)
    return best


def lane_run(cmd: str) -> int:
    """Run a check/install the way a lane would see it: in WSL (Windows) or the local shell, login PATH."""
    try:
        return subprocess.run(wsl_prefix() + ["bash", "-lc", cmd], capture_output=True, timeout=600).returncode
    except Exception:
        return 1


def preflight(proj: dict, fix: bool = True, run=lane_run) -> list:
    """Each skill: check -> (install -> re-check) -> row. A skill is usable only if its CHECK passes."""
    rows = []
    for s in proj.get("skills", []):
        ok = run(s["check"]) == 0
        installed = False
        if not ok and fix and s.get("install"):
            installed = True
            run(s["install"])
            ok = run(s["check"]) == 0
        rows.append({"skill": s["skill"], "required": s.get("required", True), "ok": ok, "installed": installed})
    return rows


def preflight_cached(proj: dict, fresh: bool = False, run=lane_run, cache=None, now=None) -> list:
    """Re-run only the checks whose own reuse window ran out: a skill's `ttl_s` (default PREFLIGHT_TTL).
    A check that costs the host something per run gets a long ttl_s; e.g. a webgrab check that launches uv's
    CPython 3.11, and every launch of that build leaks ntfs.sys NtFC pool records (measured 2026-10-28)."""
    cache = Path(cache) if cache else Path.home() / ".claude" / "fleet-preflight-cache.json"
    now = time.time() if now is None else now
    try:
        c = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    except ValueError:
        c = {}
    hit = c.get(proj.get("name", "?")) or {}
    old = {r["skill"]: r for r in hit.get("rows", [])}
    stale = []
    for s in proj.get("skills", []):
        r = old.get(s["skill"])
        ts = r.get("ts", hit.get("ts", 0)) if r else 0
        if fresh or not r or now - ts >= s.get("ttl_s", PREFLIGHT_TTL) or not (r["ok"] or not r["required"]):
            stale.append(s)
    if not stale:
        return [old[s["skill"]] for s in proj.get("skills", [])]
    new = {r["skill"]: dict(r, ts=now) for r in preflight(dict(proj, skills=stale), run=run)}
    rows = [new.get(s["skill"]) or old[s["skill"]] for s in proj.get("skills", [])]
    c[proj.get("name", "?")] = {"ts": now, "rows": rows}
    try:
        cache.write_text(json.dumps(c, indent=1), encoding="utf-8")
    except OSError:
        pass
    return rows


def copy_lane_assets(proj: dict, lane_dir: str) -> list:
    """Copy the project's `lane_assets` -- gitignored files a lane worktree needs but git does not carry
    (e.g. model files; without them a lane silently measures a different system) --
    into a lane worktree ROOT (a dir holding .git) where missing. A source whose sha256 does not start with
    its pin is refused, never copied. Returns report lines; any line not starting "copied" is a refusal."""
    root = Path(to_win_path(lane_dir) if os.name == "nt" else lane_dir)
    if not proj.get("lane_assets") or not (root / ".git").exists():
        return []
    out = []
    for a in proj["lane_assets"]:
        dst = root / a["path"]
        if dst.exists():
            continue
        src = Path(a["from"] if os.name == "nt" else to_wsl_path(a["from"]))
        if not src.is_file():
            out.append(f"MISSING source for {a['path']}: {src}")
            continue
        pin = a.get("sha256", "")
        if pin and not hashlib.sha256(src.read_bytes()).hexdigest().startswith(pin):
            out.append(f"REFUSED {a['path']}: source sha256 does not start with the pin {pin}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        out.append(f"copied {a['path']}")
    return out


def with_preamble(rest: list, proj: dict) -> list:
    """Prefix the lane's --prompt with the project's lane_preamble (e.g. 'route with the hub skill first')."""
    pre = (proj or {}).get("lane_preamble")
    if not pre or "--prompt" not in rest or rest.index("--prompt") + 1 >= len(rest):
        return rest
    j = rest.index("--prompt") + 1
    out = list(rest)
    if not out[j].startswith(pre):
        out[j] = f"{pre}\n\n{out[j]}"
    return out


# A finished or idle lane holds memory but no work slot. Every OTHER status counts as active, including ones this
# list has never seen (the binary also reports e.g. running/blocked): an unknown status errs toward holding a spawn.
IDLE = ("finished", "idle", "dead", "errored")
ADMIT_POLL = 30          # seconds between admission re-checks under --wait-admission
ADMIT_LOCK_STALE = 300   # an admission lock older than this was left by a crashed spawn


def admission(proj: dict, s, rows: list) -> str:
    """Why a spawn must wait now ('' = admitted): the project's RAM floor and active-lane cap.
    No leak-guard sample (not Windows) skips the floor, like the leak guard itself."""
    floor = proj.get("min_available_mb")
    if floor and s and s["available_mb"] < floor:
        return f"{s['available_mb']:.0f} MB available, below the project's {floor} MB floor"
    cap = proj.get("max_active_lanes")
    if cap:
        mine = [r["name"] for r in rows if r.get("status") not in IDLE and not r.get("archived")
                and project_for(r.get("directory", "")).get("name") == proj.get("name")]
        if len(mine) >= cap:
            return f"{len(mine)} active lane(s) ({', '.join(mine)}), at the project's cap of {cap}"
    return ""


def admit_lock() -> Path:
    return roster.root() / "admission.lock"


def take_admit_lock() -> bool:
    """One spawn at a time between the admission check and the spawn itself, so N queued spawns cannot all see the
    same freed slot. A lock older than ADMIT_LOCK_STALE was left by a crashed spawn and is cleared."""
    f = admit_lock()
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - f.stat().st_mtime > ADMIT_LOCK_STALE:
            f.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        os.close(os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
        return True
    except FileExistsError:
        return False


def lane_id(name: str) -> str:
    """The live agent-fleet session id of a just-spawned lane ('' when it cannot be read)."""
    try:
        return next((r["id"] for r in sessions() if r.get("name") == name and r.get("status") != "dead"
                     and not r.get("archived")), "")
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError):
        return ""


def renew_live(every: int = 0) -> int:
    """Renew the claims and takes of every roster lane whose agent-fleet session is alive. Liveness comes from the
    session, not from tool calls, so a lane inside one long tool call (no hook fires) keeps its leases; a lane with no
    live session stops renewing and its leases lapse to the next claimer. --every N repeats forever (the hidden TUI)."""
    import lanehook
    import board as B
    import fastboard
    fastboard.install()
    ttl = int(os.environ.get("LANEHOOK_TTL", "1800"))
    while True:
        try:
            rows = [r for r in sessions() if r.get("status") != "dead" and not r.get("archived")]
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as e:
            print(f"fleet: renew-live cannot read the fleet ({e}); nothing renewed", file=sys.stderr)
            rows = None
        if rows is not None:
            ids, names, n, failed = {r.get("id") for r in rows}, {r.get("name") for r in rows}, 0, []
            for name, e in roster.load().items():
                if e.get("board") and (e["id"] in ids if e.get("id") else name in names):
                    k, f = lanehook.keep_alive(B, Path(roster.native(e["board"])), name, ttl, time.time())
                    n += k; failed += f
            print(f"fleet: renew-live {time.strftime('%H:%M:%S')}: renewed {n} lease(s)"
                  + (f"; could not renew {', '.join(failed)}" if failed else ""), flush=True)
        if not every:
            return 0
        time.sleep(every)


RESUME_NOTE = ("RESUMED by fleet.py resurrect: this lane's earlier session ended (a crash or a reboot). Before acting, "
               "read your brief and the report files in this folder, and your board mail (view.py brief --as <you>); "
               "finish only what is not already done.")


def resurrect(names: list, every: bool) -> int:
    """Roster lanes with no live session: listed by default; respawned (their own spawn args, a resume note first)
    when named, or with --all every one that was cut off (its last event a tool call: a crash or a reboot hit it
    mid-work), so a lane that finished its turn or ended is never restarted by accident."""
    live = {r["name"] for r in sessions() if r.get("status") != "dead" and not r.get("archived")}
    lanes = roster.load()
    gone = [n for n in lanes if n not in live and lanes[n].get("argv")]
    cut = [n for n in gone if roster.heartbeat(n).get("last") == "tool"]
    if not names and not every:
        for n in gone:
            last = roster.heartbeat(n).get("last")
            state = {"tool": "CUT OFF (last event a tool call)", "stop": "finished its turn", "end": "ended cleanly"}
            print(f"  {n}: {state.get(last, 'never ran a tool')}; {lanes[n]['dir']} (spawned {lanes[n].get('spawned', '?')})")
        print(f"{len(gone)} lane(s) with no live session, {len(cut)} cut off; respawn with "
              f"`fleet.py resurrect NAME...`, or --all for the cut-off ones")
        return 0
    rc = 0
    for n in (cut if every else names):
        if n not in gone:
            print(f"fleet: {n} is {'live' if n in live else 'not in the roster'}; not respawned", file=sys.stderr)
            rc = max(rc, 1)
            continue
        argv = list(lanes[n]["argv"])
        if "--prompt" in argv and argv.index("--prompt") + 1 < len(argv):
            j = argv.index("--prompt") + 1
            argv[j] = f"{RESUME_NOTE}\n\n{argv[j].removeprefix(RESUME_NOTE).lstrip()}"
        r = main(["spawn", *argv])
        print(f"fleet: resurrect {n}: {'spawned' if r == 0 else f'rc {r}'}")
        rc = max(rc, r)
    return rc


def preflight_report(rows: list) -> tuple:
    bad = [r["skill"] for r in rows if r["required"] and not r["ok"]]
    lines = [f"  {'OK  ' if r['ok'] else ('FAIL' if r['required'] else 'warn')} {r['skill']}"
             + ("  (installed now)" if r["installed"] else "") for r in rows]
    return bad, "\n".join(lines)


def main(argv=None):
    # the TUI's status glyphs (e.g. U+25CF) crashed print() on a cp1252 console (`ui shot`, 2026-10-22)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog="fleet", description=__doc__.splitlines()[0])
    ap.add_argument("--board"); ap.add_argument("--project")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("am", add_help=False)
    sub.add_parser("leases").add_argument("--json", action="store_true")
    sub.add_parser("mcp-config")
    sub.add_parser("on"); sub.add_parser("off")
    sub.add_parser("trust").add_argument("dirs", nargs="+")
    sub.add_parser("spawn", add_help=False)
    sp = sub.add_parser("sessions")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--all", action="store_true", help="also list ARCHIVED sessions (hidden by default)")
    sub.add_parser("kill").add_argument("ids", nargs="+")
    u = sub.add_parser("ui"); u.add_argument("action", choices=["start", "stop", "status", "shot", "keys", "attach"])
    u.add_argument("more", nargs="*")
    sub.add_parser("archive-dead")
    sub.add_parser("build-core")
    pf = sub.add_parser("preflight", help="install and verify every skill the project's lanes need")
    pf.add_argument("--cwd", default=os.getcwd()); pf.add_argument("--no-fix", action="store_true")
    rl = sub.add_parser("renew-live", help="renew the leases of roster lanes whose session is alive")
    rl.add_argument("--every", type=int, default=0, help="repeat every N seconds (the hidden TUI runs 300)")
    rs = sub.add_parser("resurrect", help="list, or respawn, roster lanes that have no live session")
    rs.add_argument("names", nargs="*"); rs.add_argument("--all", action="store_true")
    args, rest = ap.parse_known_args(argv)

    if args.cmd == "am":
        # bash -lc: agent-fleet lives in ~/.local/bin, which only a login shell puts on PATH.
        return subprocess.call(wsl_prefix() + ["bash", "-lc", f'exec {AM} "$@"', "am"] + rest)
    if args.cmd == "spawn":
        # A folder WSL claude has never opened parks the new session on the trust prompt, unseen.
        if "--directory" not in rest or rest.index("--directory") + 1 >= len(rest):
            ap.error("spawn needs --directory DIR, so the folder can be trusted first")
        import leakguard
        lg_before = leakguard.sample()
        why = leakguard.refusal(lg_before)
        if why:
            # The ntfs.sys pool leak grows in bursts at lane spawn/archive (2026-10-28); past the limit, reboot first.
            print(f"fleet: spawn refused by the leak guard: {why}", file=sys.stderr)
            leakguard.record("spawn-refused", "", lg_before, None, follow=False)
            return 1
        if leakguard.warning(lg_before):
            print(leakguard.warning(lg_before), file=sys.stderr)
        skip = "--no-preflight" in rest
        admit = "--no-admission" not in rest
        wait_s = 0
        if "--wait-admission" in rest:
            j = rest.index("--wait-admission")
            try:
                wait_s = int(rest[j + 1])
            except (IndexError, ValueError):
                ap.error("--wait-admission needs SECONDS")
            del rest[j:j + 2]
        rest = [a for a in rest if a not in ("--no-preflight", "--no-admission")]
        i = rest.index("--directory") + 1
        rest[i] = to_wsl_path(rest[i])
        given = list(rest)
        try:
            home = project_for(rest[i])
            proj, gate = ({} if skip else home), (home if admit else {})
        except RequirementsError as e:
            print(f"fleet: spawn refused, lane requirements unreadable: {e}", file=sys.stderr)
            return 1
        if proj and not skip:
            # Lanes must be able to install and FULLY use every skill the project requires before they run.
            # A required skill whose functional check fails refuses the spawn.
            bad, report = preflight_report(preflight_cached(proj))
            if bad:
                print(f"fleet: spawn refused, {proj.get('name')} lanes cannot use: {', '.join(bad)}\n{report}\n"
                      f"fix and rerun, or `fleet.py preflight --cwd <dir>` for detail (--no-preflight overrides)", file=sys.stderr)
                return 1
            # Fail closed: a lane missing its gitignored assets would silently run a different system.
            assets = copy_lane_assets(proj, rest[i])
            for line in assets:
                print(f"lane asset: {line}")
            if any(not line.startswith("copied") for line in assets):
                print("fleet: spawn refused, lane assets could not be supplied (--no-preflight overrides)", file=sys.stderr)
                return 1
        # Admission last, just before the spawn, under one lock: the window from check to spawn is seconds, not the
        # minutes a preflight install can take, and N queued spawns cannot all take the same freed slot.
        waited, s, locked = 0, lg_before, False
        while gate:
            locked = take_admit_lock()
            why = "" if locked else "another spawn is being admitted"
            if locked:
                try:
                    rows = sessions()
                except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as e:
                    print(f"fleet: admission cannot read the fleet ({e}); cap not checked", file=sys.stderr)
                    rows = []
                why = admission(gate, s, rows)
                if why:
                    admit_lock().unlink(missing_ok=True); locked = False
            if not why:
                break
            if waited >= wait_s:
                # Held, not refused: rerun when a lane finishes, or queue with --wait-admission (lane cap + RAM floor).
                print(f"fleet: spawn not admitted: {why}. Rerun later, queue it with --wait-admission SECONDS, "
                      f"or override with --no-admission", file=sys.stderr)
                return 3
            print(f"fleet: spawn queued: {why}; rechecking in {ADMIT_POLL}s ({waited}/{wait_s}s waited)", file=sys.stderr)
            time.sleep(ADMIT_POLL)
            waited += ADMIT_POLL
            s = leakguard.sample()
        try:
            if trust([rest[i]]) != 0:
                return 1
            rest = with_preamble(rest, proj)
            name = rest[rest.index("--name") + 1] if "--name" in rest else ""
            if proj.get("board") and name:
                # Enforce board claims at commit time in the lane's worktree, under the lane's name.
                lane_dir = to_win_path(rest[i]) if os.name == "nt" else rest[i]
                try:
                    import claim_guard
                    if subprocess.run(["git", "-C", lane_dir, "rev-parse", "--is-inside-work-tree"],
                                      capture_output=True, text=True).stdout.strip() == "true":
                        print(f"claim guard: {claim_guard.install(lane_dir, proj['board'], name)} (lane {name})")
                except Exception as e:
                    print(f"fleet: spawn refused, claim guard could not be installed ({e})", file=sys.stderr)
                    return 1
            try:  # clear finished lanes first, so the TUI lists what is running, not what died earlier
                cleared = archive_dead()
                if cleared:
                    print(f"fleet: archived {len(cleared)} dead session(s) before spawning: {', '.join(cleared)}")
            except RuntimeError as e:
                print(e, file=sys.stderr)
            rc = subprocess.call(wsl_prefix() + ["bash", "-lc", f'exec {AM} spawn "$@"', "am"] + rest)
            if rc == 0 and home.get("board") and name:
                # Recorded only once the lane exists, with its session id: lanehook.py tells lanes sharing a folder apart.
                roster.record(name, dir=rest[i], board=home["board"], argv=given, id=lane_id(name))
            leakguard.record("spawn", name, lg_before, leakguard.sample())
            return rc
        finally:
            if locked:
                admit_lock().unlink(missing_ok=True)
    if rest:
        ap.error(f"unrecognized arguments: {' '.join(rest)}")
    if args.cmd == "trust":
        return trust(args.dirs)
    if args.cmd == "preflight":
        try:
            proj = project_for(args.cwd)
        except RequirementsError as e:
            print(f"fleet: {e}", file=sys.stderr); return 2
        if not proj:
            print(f"fleet: no requirements entry covers {args.cwd} (~/.claude/fleet-requirements.json)"); return 0
        rows = preflight(proj, fix=not args.no_fix)
        bad, report = preflight_report(rows)
        print(f"preflight {proj.get('name')}: {len(rows) - len(bad)}/{len(rows)} usable\n{report}")
        try:
            cache = Path.home() / ".claude" / "fleet-preflight-cache.json"
            c = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
            c[proj.get("name", "?")] = {"ts": time.time(), "rows": rows}
            cache.write_text(json.dumps(c, indent=1), encoding="utf-8")
        except (OSError, ValueError):
            pass
        return 1 if bad else 0
    if args.cmd == "sessions":
        rows = sessions()
        # Archived rows are history, not agents: listing them as "dead" read as 30+ failed agents (seen 2026-10-15).
        hidden = 0 if args.all else sum(1 for r in rows if r.get("archived"))
        rows = rows if args.all else [r for r in rows if not r.get("archived")]
        for r in rows:
            flag = f"  BLOCKED: {r['blocked']}" if r["blocked"] else ""
            print(json.dumps(r) if args.json else f"{r['id']} {r['status']:9} {r['tool']:7} {r['name']}  {r['directory']}{flag}")
        if not args.json:
            print(f"{len(rows)} session(s), {sum(1 for r in rows if r['blocked'])} need attention"
                  + (f", {hidden} archived hidden (--all to list)" if hidden else ""))
        return 0
    if args.cmd == "ui":
        if args.action == "start":
            r = ui_start(); print(f"hidden TUI: {r}"); return 0 if r in ("running", "started") else 1
        if args.action == "stop":
            ui_tmux("kill-server"); print("hidden TUI stopped"); return 0
        if args.action == "status":
            up = ui_running(); print(f"hidden TUI {'running' if up else 'not running'}; watch it: {attach_cmd()}"); return 0 if up else 1
        if args.action == "attach":
            print(attach_cmd()); return 0
        if not ui_running():
            print("fleet: hidden TUI not running (fleet.py ui start)", file=sys.stderr); return 1
        if args.action == "keys":
            ui_keys(args.more)
        lines = ui_shot().splitlines()
        n = int(args.more[0]) if args.action == "shot" and args.more else len(lines)
        print("\n".join(lines[:n])); return 0
    if args.cmd == "build-core":
        return build_core()
    if args.cmd == "resurrect":
        return resurrect(args.names, args.all)
    if args.cmd == "renew-live":
        return renew_live(args.every)
    if args.cmd == "archive-dead":
        import leakguard
        lg_before = leakguard.sample()
        done = archive_dead()
        leakguard.record("archive-dead", ",".join(done), lg_before, leakguard.sample())
        left = [r["name"] for r in sessions() if r.get("status") == "dead" and not r.get("archived")]
        print(f"archived {len(done)}: {', '.join(done) or '-'}" + (f"; still dead, not archived: {', '.join(left)}" if left else ""))
        return 1 if left else 0
    if args.cmd == "kill":
        import leakguard
        lg_before = leakguard.sample()
        rc = 0
        try:
            id_name = {r["id"]: r["name"] for r in sessions()}
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError):
            id_name = {}
        for sid in args.ids:
            if not re.fullmatch(r"[0-9a-f]{8}", sid):
                print(f"fleet: {sid!r} is not an agent-fleet session id", file=sys.stderr); rc = 1; continue
            p = in_wsl(["tmux", "-L", TMUX_SOCKET, "kill-session", "-t", f"am_{sid}"])
            print(f"killed {sid}" if p.returncode == 0 else f"fleet: {sid}: {p.stderr.strip()}")
            rc |= p.returncode != 0
            if p.returncode == 0:   # stopped on purpose: never resurrected (archiving alone does not forget a lane)
                roster.forget([id_name[sid]] if sid in id_name else [])
        killed = [s for s in args.ids if re.fullmatch(r"[0-9a-f]{8}", s)]
        if rc == 0 and killed:
            # A killed lane is finished business: file it away so the TUI shows only what still runs.
            ui_start()
            await_dead(killed)
            done = archive_dead()
            print(f"archived {len(done)}: {', '.join(done) or '-'}")
        if killed:  # an invalid id runs nothing, so it is no lifecycle step to log
            leakguard.record("kill", ",".join(killed), lg_before, leakguard.sample())
        return rc
    if args.cmd == "leases":
        ls = leases()
        for l in ls:
            print(json.dumps(l) if args.json else f"{l['mode']:9} {l['pattern']}  ({l['session_id']}) {l['note']}")
        if not args.json:
            print(f"{len(ls)} live lease(s)")
        return 0
    if args.cmd == "mcp-config":
        cmd = wsl_prefix() + ["bash", "-lc", f"exec {AM} mcp"]
        print(json.dumps({"agent-fleet": {"type": "stdio", "command": cmd[0], "args": cmd[1:]}}, indent=2))
        print("claude mcp add -s user agent-fleet -- " + " ".join(
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
