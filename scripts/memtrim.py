#!/usr/bin/env python3
"""Keep Claude Code memory files under a byte budget WITHOUT losing a line (any project).

A trim is a MOVE, never a delete: Claude condenses an oversized memory file (each section keeps its rule in
1-3 lines plus a pointer) and moves the full original text, verbatim, into a DETAIL file. `verify` proves it:
every non-blank line of the pre-trim snapshot must still exist verbatim in the file, its DETAIL file, or
another file the config names (ledgers, handoffs), and the file must fit its budget. Claude Code also
auto-loads each project's MEMORY.md index and truncates it past a size limit, so that is guarded too.

Config: $MEMTRIM_CONFIG or ~/.claude/memtrim.json, e.g.
  {"projects": [{"cwd_prefix": "D:\\\\work\\\\app",              # sessions launched under this folder
                 "files": [{"file": "START-HERE.md", "detail": "START-HERE-DETAIL.md", "budget": 8192,
                            "corpus": ["history-*.md", "handoff-*.md"]}],
                 "index_cap": 24400}]}                         # optional; MEMORY.md warn at 90%
memory_dir defaults to ~/.claude/projects/<cwd with : \\ / turned into ->/memory (Claude Code's own layout).
With no config entry for a project, only its MEMORY.md is guarded (cap 24400 bytes).
MEMORY.md needs no spec to trim: snapshot/verify fall back to a built-in one (budget 70% of index_cap, corpus *.md,
so a moved line may land in any top-level memory file); the hook still only warns for it.

  memtrim.py status [--cwd DIR]          sizes against budgets
  memtrim.py snapshot FILE [--cwd DIR]   copy FILE to <memory_dir>/archive/memtrim-snapshots/ before trimming
  memtrim.py verify SNAPSHOT [--cwd DIR] exit 0 only if the file fits and no snapshot line was lost
  memtrim.py hook                        SessionStart (stdin JSON with cwd): inject the trim instruction / index warning
  memtrim.py selftest                    reachable reds for verify + a passing lossless move

Off: MEMTRIM_OFF=1. The hook fails open.
ponytail: matching is per stripped line, so a short line that also occurs elsewhere passes even if its own
copy was dropped. Upgrade path: match moved blocks as ordered runs.
"""
import contextlib, io, json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

INDEX, INDEX_CAP, SNAPS = "MEMORY.md", 24400, "archive/memtrim-snapshots"


def memory_dir_for(cwd: str) -> Path:
    return Path.home() / ".claude" / "projects" / re.sub(r"[:\\/]", "-", str(cwd).rstrip("\\/")) / "memory"


def load_config() -> dict:
    p = Path(os.environ.get("MEMTRIM_CONFIG") or Path.home() / ".claude" / "memtrim.json")
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except ValueError as e:  # say so: a silent {} turns a config typo into a misleading "no spec" later
        print(f"memtrim: bad config {p}: {e}", file=sys.stderr)
        return {}


def project_for(cwd: str) -> dict:
    """The config entry whose cwd_prefix contains cwd (longest wins), or a MEMORY.md-only default."""
    norm = os.path.normcase(os.path.abspath(cwd))
    hits = [p for p in load_config().get("projects", [])
            if norm.startswith(os.path.normcase(os.path.abspath(p["cwd_prefix"])))]
    proj = dict(max(hits, key=lambda p: len(p["cwd_prefix"]))) if hits else {"cwd_prefix": cwd, "files": []}
    proj["memory_dir"] = Path(proj.get("memory_dir") or memory_dir_for(proj["cwd_prefix"]))
    proj.setdefault("files", []); proj.setdefault("index_cap", INDEX_CAP)
    return proj


def size(p: Path) -> int:
    return p.stat().st_size if p.exists() else 0


def body_lines(text: str) -> list:
    """Non-blank lines outside the YAML frontmatter, whitespace-normalized."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        text = text[end + 4:] if end != -1 else text
    return [" ".join(l.split()) for l in text.splitlines() if l.strip()]


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def verify(snapshot: Path, spec: dict, mem: Path) -> list:
    f = mem / spec["file"]
    probs = [f"{spec['file']} is {size(f)} bytes, budget {spec['budget']}"] if size(f) > spec["budget"] else []
    files = [f, mem / spec["detail"]] + [g for pat in spec.get("corpus", []) for g in sorted(mem.glob(pat))]
    have = {l for g in files if g.exists() for l in body_lines(read(g))}
    lost = [l for l in body_lines(read(snapshot)) if l not in have]
    return probs + [f"LOST (in no kept file): {l[:120]}" for l in lost]


def snapshot(spec: dict, mem: Path) -> Path:
    d = mem / SNAPS; d.mkdir(parents=True, exist_ok=True)
    p = d / f"{Path(spec['file']).stem}-{time.strftime('%Y%m%d-%H%M%S')}.md"
    shutil.copyfile(mem / spec["file"], p)
    return p


def specs(proj: dict) -> list:
    """Configured specs plus a built-in MEMORY.md spec (70% of index_cap, corpus = every top-level memory file), so an
    index trim can be snapshotted and verified with no config edit. snapshot/verify only: the hook keeps proj["files"],
    because a MEMORY.md spec there would snapshot the index at every session start while it is over budget."""
    if any(s["file"] == INDEX for s in proj["files"]):
        return proj["files"]
    return proj["files"] + [{"file": INDEX, "detail": INDEX, "budget": proj["index_cap"] * 7 // 10, "corpus": ["*.md"]}]


def spec_for_snapshot(snap: Path, proj: dict):
    return next((s for s in specs(proj) if snap.name.startswith(Path(s["file"]).stem + "-")), None)


def no_spec(name: str, proj: dict) -> int:
    print(f"memtrim: no spec for {name}; known: {', '.join(s['file'] for s in specs(proj))} "
          f"(config: $MEMTRIM_CONFIG or ~/.claude/memtrim.json)")
    return 2


def instruction(spec: dict, mem: Path, snap: Path) -> str:
    me = Path(__file__).resolve()
    return (f"MEMORY TRIM DUE: {mem / spec['file']} is {size(mem / spec['file'])} bytes, budget {spec['budget']}; trim it "
            f"between sessions and lose nothing. Pre-trim snapshot: {snap}. Before other work: (1) condense each section "
            f"to its rule in 1-3 lines (keep numbers, bold rule names, quoted instructions and dates) with a pointer to "
            f"{spec['detail']}; (2) move each section's FULL original text VERBATIM (byte-identical lines; do not fix "
            f"wording or encoding while moving) into {mem / spec['detail']} under a heading per section; (3) run: "
            f"python \"{me}\" verify \"{snap}\" --cwd \"<this session's cwd>\" and repeat until it exits 0. "
            f"Never delete a rule to make it fit.")


def hook(stdin_text: str) -> str:
    if os.environ.get("MEMTRIM_OFF") == "1":
        return ""
    try:
        cwd = json.loads(stdin_text.lstrip("﻿") or "{}").get("cwd") or os.getcwd()
    except ValueError:
        cwd = os.getcwd()
    proj = project_for(cwd); mem = proj["memory_dir"]; notes = []
    for spec in proj["files"]:
        if size(mem / spec["file"]) > spec["budget"]:
            notes.append(instruction(spec, mem, snapshot(spec, mem)))
    i, cap = size(mem / INDEX), proj["index_cap"]
    if i >= 0.9 * cap:
        notes.append(f"{mem / INDEX} is {i} bytes, {i * 100 // cap}% of its {cap}-byte cap. Claude Code auto-loads it and "
                     f"truncates past the limit, so prune or archive index lines now (move detail into topic files).")
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                              "additionalContext": " ".join(notes)}}) if notes else ""


def selftest() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="memtrim-")); res = []
    cfg = tmp / "memtrim.json"; mem = tmp / "mem"; mem.mkdir()
    spec = {"file": "START.md", "detail": "START-DETAIL.md", "budget": 8192, "corpus": ["ledger-*.md"]}
    cfg.write_text(json.dumps({"projects": [{"cwd_prefix": str(tmp / "proj"), "memory_dir": str(mem), "files": [spec]}]}))
    old = os.environ.get("MEMTRIM_CONFIG"); os.environ["MEMTRIM_CONFIG"] = str(cfg)
    try:
        rule = "1. **Rule one (2026-01-01: \"keep it\"):** first line of a long item"
        tail = [f"   detail line {k} " + "x" * 60 for k in range(140)]
        (mem / "START.md").write_text("---\nname: s\n---\n# PROTOCOL\n" + rule + "\n" + "\n".join(tail) + "\n# STATE\nnow\n", encoding="utf-8")
        snap = snapshot(spec, mem)
        res.append(("oversize_refused", any("budget" in p for p in verify(snap, spec, mem))))
        (mem / "START.md").write_text("---\nname: s\n---\n# PROTOCOL\n" + rule + " (full text: DETAIL 1)\n# STATE\nnow\n", encoding="utf-8")
        # the condensed rule line differs from its original, so 140 tail lines + the original rule line are lost
        res.append(("dropped_lines_refused", sum("LOST" in p for p in verify(snap, spec, mem)) == 141))
        (mem / "START-DETAIL.md").write_text("## 1\n" + rule + "\n" + "\n".join(tail[:-1]) + "\n", encoding="utf-8")
        res.append(("one_missing_line_named", [p for p in verify(snap, spec, mem) if "LOST" in p]
                    == [f"LOST (in no kept file): {' '.join(tail[-1].split())[:120]}"]))
        (mem / "START-DETAIL.md").write_text("## 1\n" + rule + "\n" + "\n".join(tail[:-2]) + "\n", encoding="utf-8")
        (mem / "ledger-1.md").write_text("\n".join(tail[-2:]) + "\n", encoding="utf-8")
        res.append(("corpus_file_counts", verify(snap, spec, mem) == []))
        res.append(("frontmatter_ignored", body_lines("---\nmodified: 1\n---\nA\n") == ["A"]))
        res.append(("config_resolves_project", project_for(str(tmp / "proj" / "sub"))["memory_dir"] == mem))
        res.append(("default_memory_dir", memory_dir_for("D:\\work\\app") == Path.home() / ".claude/projects/D--work-app/memory"))
        (mem / "START.md").write_text("x" * 9000, encoding="utf-8")
        out = hook(json.dumps({"cwd": str(tmp / "proj")}))
        res.append(("hook_instructs_when_over", "MEMORY TRIM DUE" in out and "verify" in out))
        res.append(("hook_bom_payload_parsed", "MEMORY TRIM DUE" in hook("﻿" + json.dumps({"cwd": str(tmp / "proj")}))))
        res.append(("hook_other_project_silent", hook(json.dumps({"cwd": str(tmp / "elsewhere")})) == ""))
        (mem / "START.md").write_text("small", encoding="utf-8")
        res.append(("hook_silent_when_fits", hook(json.dumps({"cwd": str(tmp / "proj")})) == ""))
        (mem / INDEX).write_text("y" * 23000, encoding="utf-8")
        res.append(("hook_warns_index_90pct", "auto-loads it" in hook(json.dumps({"cwd": str(tmp / "proj")}))))

        def chk(fn):  # a crash in the code under test is a FAIL, not a selftest abort
            try:
                return bool(fn())
            except Exception:
                return False

        def run(*a):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["memtrim.py", *a, "--cwd", str(tmp / "proj")])
            return rc, buf.getvalue()
        # a refused verify must list a LOST line that holds a non-cp1252 character, not crash on it
        (mem / "START.md").write_text("# s\n⛔ rule kept nowhere else\n", encoding="utf-8")
        ssnap = snapshot(spec, mem)
        (mem / "START.md").write_text("# s\n", encoding="utf-8")
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "verify", str(ssnap), "--cwd", str(tmp / "proj")],
                           capture_output=True, env=dict(os.environ, PYTHONIOENCODING="cp1252"))
        res.append(("lost_line_prints_on_cp1252_console",
                    p.returncode == 1 and b"LOST" in p.stdout and b"Traceback" not in p.stderr))
        # MEMORY.md snapshots and verifies with no spec in the config (built-in index spec)
        (mem / INDEX).write_text("# idx\n- [a](a.md) — ⛔ long hook kept only here\n", encoding="utf-8")
        (mem / "a.md").write_text("a\n", encoding="utf-8")
        res.append(("index_snapshot_without_spec", chk(lambda: run("snapshot", INDEX)[0] == 0)))
        isnap = next(iter(sorted((mem / SNAPS).glob("MEMORY-*.md"))), None)
        (mem / INDEX).write_text("# idx\n- [a](a.md)\n", encoding="utf-8")
        res.append(("index_verify_refuses_unmoved_line", chk(lambda: run("verify", str(isnap))[0] == 1)))
        (mem / "a.md").write_text("a\n- [a](a.md) — ⛔ long hook kept only here\n", encoding="utf-8")
        res.append(("index_verify_accepts_moved_line", chk(lambda: run("verify", str(isnap))[0] == 0)))
        try:
            urc, uout = run("snapshot", "nope.md")
        except Exception:
            urc, uout = None, ""
        res.append(("unknown_file_named_not_stopiteration", urc == 2 and "nope.md" in uout))
        # the built-in index spec is for snapshot/verify only: the SessionStart hook never snapshots MEMORY.md
        (mem / INDEX).write_text("z" * 20000, encoding="utf-8")
        before = len(list((mem / SNAPS).glob("MEMORY-*.md")))
        out = hook(json.dumps({"cwd": str(tmp / "proj")}))
        res.append(("hook_never_snapshots_index",
                    "MEMORY TRIM DUE" not in out and len(list((mem / SNAPS).glob("MEMORY-*.md"))) == before))
        # a config that does not parse is reported, not silently treated as absent
        bad = tmp / "bad.json"; bad.write_text('{"projects": [{"cwd_prefix": "C:\\x"}]}', encoding="utf-8")
        os.environ["MEMTRIM_CONFIG"] = str(bad); err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = load_config()
        os.environ["MEMTRIM_CONFIG"] = str(cfg)
        res.append(("bad_config_reported", got == {} and "bad.json" in err.getvalue()))
    finally:
        if old is None: os.environ.pop("MEMTRIM_CONFIG", None)
        else: os.environ["MEMTRIM_CONFIG"] = old
        shutil.rmtree(tmp, ignore_errors=True)
    for n, ok in res:
        print(f"{'PASS' if ok else 'FAIL'}  memtrim: {n}")
    bad = sum(not ok for _, ok in res)
    print("ALL GREEN" if not bad else f"{bad} FAILED"); return 1 if bad else 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):  # memory text holds non-cp1252 chars (e.g. the stop sign); never crash on a RED
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = list(argv[1:]); cwd = os.getcwd()
    if "--cwd" in args:
        i = args.index("--cwd"); cwd = args[i + 1]; del args[i:i + 2]
    cmd = args[0] if args else "status"
    proj = project_for(cwd); mem = proj["memory_dir"]
    if cmd == "status":
        over = 0
        for s in proj["files"]:
            n = size(mem / s["file"]); over |= n > s["budget"]
            print(f"{s['file']}: {n} / {s['budget']} bytes{'  OVER' if n > s['budget'] else ''}")
        i = size(mem / INDEX)
        print(f"{INDEX}: {i} / {proj['index_cap']} bytes ({i * 100 // proj['index_cap']}%)")
        return 1 if over else 0
    if cmd == "snapshot":
        spec = next((s for s in specs(proj) if s["file"] == args[1]), None)
        if spec is None:
            return no_spec(args[1], proj)
        print(snapshot(spec, mem)); return 0
    if cmd == "verify":
        snap = Path(args[1]); spec = spec_for_snapshot(snap, proj)
        if spec is None:
            return no_spec(snap.name, proj)
        probs = verify(snap, spec, mem)
        for p in probs: print("RED", p)
        print(f"{'OK' if not probs else 'REFUSED'}: {len(probs)} problem(s)"); return 1 if probs else 0
    if cmd == "hook":
        try:
            # utf-8-sig: a PowerShell pipe prepends a BOM, and an unparsed payload would fall back to the wrong cwd.
            out = hook(sys.stdin.buffer.read().decode("utf-8-sig", errors="replace"))
            if out: print(out)
        except Exception:
            pass
        return 0
    if cmd == "selftest":
        return selftest()
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
