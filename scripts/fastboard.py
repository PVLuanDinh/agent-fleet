#!/usr/bin/env python3
"""Fast board loading: the same list board.load_msgs returns, without re-reading every message file.

A board's msgs/ only grows, and a message file never changes once written, so the parsed messages
are cached in <board>/cache/msgs-index.json keyed by file name, size and modification time. A load
lists the directory once (a few ms for thousands of files), reuses every unchanged entry and reads
only new or changed files. A cold load (no cache, or many new files) reads them in parallel through
the Rust helper bin/fleetcore when it is built (`python fleet.py build-core`), else sequentially.

Measured on a 4,315-message board (2026-10-19, Windows): board.load_msgs 266 ms warm / 620 ms cold;
warm cache ~30 ms; cold Rust read 129 ms. The cache is JSON, never pickle: other agents can write
into the board directory, and unpickling their file would run their code.

Integrity: a file whose size and mtime are unchanged is trusted from the cache. On a slow bridged
filesystem (a Windows drive seen from WSL, /mnt/<drive>, where one stat per file costs ~0.3 ms and the
uncached load took 11.3 s) files are matched by NAME only, from one directory listing, and the cache
lives on the local disk (~/.cache/agent-fleet/boards): message files are written once and never
rewritten (board.py hard-links them into place), so a name is the identity. FLEET_NOCACHE=1 (or
load_msgs(root, strict=True)) re-reads every file; FLEET_SLOWFS=1/0 forces the mode. score.py keeps
the uncached board.py loader, so anchored scoring never depends on this cache.

  python fastboard.py --board B bench      time cached vs uncached loads on a board
"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import board as B  # noqa: E402

CACHE_VERSION = 1
RUST_MIN_MISSES = 64  # below this, starting the helper costs more than it saves
CORE = HERE / "bin" / ("fleetcore.exe" if os.name == "nt" else "fleetcore")


def _parse(name: str, text) -> dict:
    """One message exactly as board.load_msgs builds it, corrupt files included."""
    try:
        if isinstance(text, Exception):
            raise text
        d = json.loads(text)
        if not isinstance(d, dict) or not isinstance(d.get("seq"), int) or isinstance(d["seq"], bool):
            raise ValueError("not a message object with an int seq")
    except (OSError, ValueError) as e:
        d = {"seq": -1, "_corrupt": str(e)}
    d["_file"] = name
    return d


def _read_python(mdir: Path, names: list) -> dict:
    out = {}
    for n in names:
        try:
            out[n] = (mdir / n).read_text(encoding="utf-8")
        except OSError as e:
            out[n] = e
    return out


def _read_rust(mdir: Path, names: list) -> dict:
    """Bulk-read through bin/fleetcore; returns {} when the helper is missing or fails (caller falls back)."""
    if not CORE.exists():
        return {}
    try:
        p = subprocess.run([str(CORE), str(mdir)], capture_output=True, timeout=120)
        if p.returncode != 0:
            return {}
        rows = json.loads(p.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    want = set(names)
    return {r[0]: r[3] for r in rows if r[0] in want}


def _slow_fs(root: Path) -> bool:
    forced = os.environ.get("FLEET_SLOWFS")
    if forced in ("0", "1"):
        return forced == "1"
    return os.name != "nt" and str(root.resolve()).startswith("/mnt/")


def _cache_path(root: Path) -> Path:
    if _slow_fs(root):
        import hashlib
        key = hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:16]
        return Path.home() / ".cache" / "agent-fleet" / "boards" / f"{key}.json"
    return root / "cache" / "msgs-index.json"


def _load_cache(root: Path) -> dict:
    try:
        data = json.loads(_cache_path(root).read_text(encoding="utf-8"))
        if data.get("v") == CACHE_VERSION and isinstance(data.get("files"), dict):
            return data["files"]
    except (OSError, ValueError):
        pass
    return {}


def _save_cache(root: Path, files: dict) -> None:
    path = _cache_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".msgs-index-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"v": CACHE_VERSION, "files": files}, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass  # a read-only board still loads, just without the speedup next time


def load_msgs(root: Path, strict: bool = None) -> list:
    """Drop-in for board.load_msgs: same list, same order, same corrupt-file entries."""
    root = Path(root)
    mdir = root / "msgs"
    if strict is None:
        strict = os.environ.get("FLEET_NOCACHE") == "1"
    slow = _slow_fs(root)
    try:
        if slow:  # one listing, no per-file stat: names are identities on a write-once board
            entries = sorted((n, None, None) for n in os.listdir(mdir) if n.endswith(".json"))
        else:
            entries = sorted((e.name, e.stat().st_size, e.stat().st_mtime_ns) for e in os.scandir(mdir)
                             if e.name.endswith(".json") and e.is_file())
    except OSError:
        return []
    cache = {} if strict else _load_cache(root)
    misses = [n for n, size, mtime in entries
              if not (n in cache and (slow or (cache[n][0] == size and cache[n][1] == mtime)))]
    texts = {}
    if misses:
        if len(misses) >= RUST_MIN_MISSES:
            texts = _read_rust(mdir, misses)
        rest = [n for n in misses if n not in texts]
        if rest:
            texts.update(_read_python(mdir, rest))
    out, fresh = [], {}
    for n, size, mtime in entries:
        if n in texts:
            d = _parse(n, texts[n])
        else:
            d = cache[n][2]
            d["_file"] = n
        out.append(d)
        stored = {k: v for k, v in d.items() if k != "_file"}
        fresh[n] = [size, mtime, stored]
    if misses or len(fresh) != len(cache):
        _save_cache(root, fresh)
    return out


def install() -> None:
    """Route board.load_msgs through the cache for this process (board.py's file stays untouched)."""
    B.load_msgs = load_msgs


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="fastboard")
    ap.add_argument("--board"); ap.add_argument("--project")
    ap.add_argument("cmd", choices=["bench"])
    a = ap.parse_args(argv)
    root = B.board_root(a)
    orig = B.__dict__.get("_orig_load_msgs", B.load_msgs)
    for name, fn in [("board.load_msgs (uncached)", lambda: orig(root)),
                     ("fastboard cold (no cache)", lambda: load_msgs(root, strict=True)),
                     ("fastboard warm (cache)", lambda: load_msgs(root))]:
        best = 1e9
        for _ in range(3):
            t = time.perf_counter(); n = len(fn()); best = min(best, time.perf_counter() - t)
        print(f"{name:30} {best * 1000:7.1f} ms  ({n} msgs)")
    print("rust helper:", CORE if CORE.exists() else "not built (python fleet.py build-core)")
    return 0


B._orig_load_msgs = B.__dict__.get("_orig_load_msgs", B.load_msgs)

if __name__ == "__main__":
    sys.exit(main())
