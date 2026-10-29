#!/usr/bin/env python3
"""Kernel-memory leak guard for the fleet (Windows host only).

Measured 2026-10-28 on a real host: after 4.8 days the kernel nonpaged pool held 16 GB of 31.8 GB, 13.1 GB of it
under pool tag NtFC (ntfs.sys), and it grew in bursts of about 4,400 records when a lane was spawned or archived.
No script can stop a driver leak, so this guard does the two things a script can do:

  1. MEASURE: nonpaged pool and the watched tag (NtFC by default) before and after every fleet spawn, kill and
     archive-dead, plus one follow-up sample about 45 s later, as JSON lines in ~/.claude/agent-fleet-leak.log.
     That log is what attributes the bursts to a lifecycle step.
  2. REFUSE a new spawn while the nonpaged pool is at or above the hard limit (default 8,192 MB), warning from
     4,096 MB: past that, more lanes only push the host toward paging and stalls, and a reboot is the fix.

  leakguard.py sample          print one sample as JSON
  leakguard.py check           exit 1 (and say why) when a spawn would be refused now
  leakguard.py follow OP ID    (internal) sleep FLEET_LEAK_FOLLOW_S, append one follow-up sample for OP/ID

Env: FLEET_NONPAGED_MAX_MB (8192), FLEET_NONPAGED_WARN_MB (4096), FLEET_LEAK_TAG (NtFC),
FLEET_LEAK_FOLLOW_S (45), FLEET_LEAK_LOG (log path), FLEET_LEAKGUARD_OFF=1 (no refusal; samples still logged).

ponytail: Windows only (NtQuerySystemInformation class 22, the call poolmon uses; no admin). From WSL or any
non-Windows host sample() is None and the guard never refuses, so a fleet.py run inside WSL is unguarded.
Upgrade path: have the WSL side call powershell.exe for the sample.
"""
import ctypes, json, os, subprocess, sys, time
from pathlib import Path

POOLTAG_CLASS = 22           # SystemPoolTagInformation
ENTRY = 40                   # x64 SYSTEM_POOLTAG: tag[4] pagedAllocs pagedFrees pad pagedUsed npAllocs npFrees npUsed
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def log_path() -> Path:
    return Path(os.environ.get("FLEET_LEAK_LOG") or Path.home() / ".claude" / "agent-fleet-leak.log")


def parse_pool_tags(buf: bytes) -> dict:
    """{tag: (nonpaged_bytes, nonpaged_live_allocs)} from a SystemPoolTagInformation buffer (x64 layout)."""
    count = int.from_bytes(buf[0:4], "little")
    out = {}
    for i in range(count):
        e = buf[8 + i * ENTRY: 8 + (i + 1) * ENTRY]
        if len(e) < ENTRY:
            break
        tag = e[0:4].decode("ascii", "replace")
        np_allocs = int.from_bytes(e[24:28], "little")
        np_frees = int.from_bytes(e[28:32], "little")
        out[tag] = (int.from_bytes(e[32:40], "little"), np_allocs - np_frees)
    return out


def _pool_buffer() -> bytes:
    nt = ctypes.WinDLL("ntdll")
    size = 1 << 20
    while True:
        buf = ctypes.create_string_buffer(size)
        ret = ctypes.c_ulong(0)
        st = nt.NtQuerySystemInformation(POOLTAG_CLASS, buf, size, ctypes.byref(ret)) & 0xFFFFFFFF
        if st == STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, ret.value + 4096)
            continue
        if st != 0:
            raise OSError(f"NtQuerySystemInformation status 0x{st:08X}")
        return buf.raw


def _nonpaged_mb() -> tuple:
    """(kernel nonpaged MB, physical available MB) from GetPerformanceInfo."""
    class PERF(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong)] + [(n, ctypes.c_size_t) for n in (
            "CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal", "PhysicalAvailable", "SystemCache",
            "KernelTotal", "KernelPaged", "KernelNonpaged", "PageSize")] + [
            (n, ctypes.c_ulong) for n in ("HandleCount", "ProcessCount", "ThreadCount")]
    p = PERF(); p.cb = ctypes.sizeof(PERF)
    if not ctypes.WinDLL("psapi").GetPerformanceInfo(ctypes.byref(p), p.cb):
        raise OSError("GetPerformanceInfo failed")
    return round(p.KernelNonpaged * p.PageSize / 2**20, 1), round(p.PhysicalAvailable * p.PageSize / 2**20, 1)


def sample():
    """One reading, or None where it cannot be taken (not Windows, or the API refused)."""
    if os.name != "nt":
        return None
    try:
        tag = os.environ.get("FLEET_LEAK_TAG", "NtFC")
        np_mb, avail_mb = _nonpaged_mb()
        used, live = parse_pool_tags(_pool_buffer()).get(tag, (0, 0))
        return {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "nonpaged_mb": np_mb, "available_mb": avail_mb,
                "tag": tag, "tag_mb": round(used / 2**20, 1), "tag_allocs": live}
    except (OSError, AttributeError, ValueError):
        return None


def refusal(s) -> str:
    """Why a spawn must be refused now ('' = allowed). No sample, or FLEET_LEAKGUARD_OFF=1, never refuses."""
    if not s or os.environ.get("FLEET_LEAKGUARD_OFF") == "1":
        return ""
    hard = _env_int("FLEET_NONPAGED_MAX_MB", 8192)
    if s["nonpaged_mb"] >= hard:
        return (f"kernel nonpaged pool is {s['nonpaged_mb']:.0f} MB, at or above the {hard} MB limit "
                f"({s['tag']} {s['tag_mb']:.0f} MB in {s['tag_allocs']:,} records; the ntfs.sys leak). "
                f"Reboot Windows to clear it, or set FLEET_NONPAGED_MAX_MB / FLEET_LEAKGUARD_OFF=1 to override.")
    return ""


def warning(s) -> str:
    if not s:
        return ""
    warn = _env_int("FLEET_NONPAGED_WARN_MB", 4096)
    if s["nonpaged_mb"] >= warn:
        return (f"leak guard: kernel nonpaged pool {s['nonpaged_mb']:.0f} MB (warn at {warn} MB, refuse at "
                f"{_env_int('FLEET_NONPAGED_MAX_MB', 8192)} MB); {s['tag']} {s['tag_mb']:.0f} MB. Plan a reboot.")
    return ""


def record(op: str, ident: str, before, after, follow: bool = True) -> None:
    """Append one JSON line for a lifecycle step; start a detached follow-up sample when asked. Never raises."""
    try:
        row = {"op": op, "id": ident, "before": before, "after": after}
        if before and after:
            row["delta_tag_allocs"] = after["tag_allocs"] - before["tag_allocs"]
            row["delta_nonpaged_mb"] = round(after["nonpaged_mb"] - before["nonpaged_mb"], 1)
        p = log_path(); p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        if follow and before and os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "follow", op, ident,
                              str(before["tag_allocs"]), str(before["nonpaged_mb"])],
                             creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
    except Exception:
        pass


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a[0] if a else "sample"
    if cmd == "sample":
        print(json.dumps(sample())); return 0
    if cmd == "check":
        s = sample(); why = refusal(s)
        print(why or warning(s) or f"leak guard: ok ({json.dumps(s)})")
        return 1 if why else 0
    if cmd == "follow" and len(a) >= 5:
        time.sleep(_env_int("FLEET_LEAK_FOLLOW_S", 45))
        s = sample()
        row = {"op": a[1] + "+follow", "id": a[2], "after": s}
        if s:
            row["delta_tag_allocs"] = s["tag_allocs"] - int(a[3])
            row["delta_nonpaged_mb"] = round(s["nonpaged_mb"] - float(a[4]), 1)
        with log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return 0
    print(__doc__.strip().splitlines()[0]); return 2


if __name__ == "__main__":
    sys.exit(main())
