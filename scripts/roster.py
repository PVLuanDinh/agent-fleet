"""The lane roster and lane heartbeats, beside the Windows ~/.claude (WSL lanes reach it through /mnt/c).

One JSON file per lane under agent-fleet-lanes/ ({dir, board, argv, id, spawned}), so concurrent spawns never lose
each other's entries the way a shared read-modify-write file does. heartbeat/<lane>.json holds the lane's LAST event:
"tool" (it was mid-work), "stop" (its turn ended) or "end" (its session ended). A lane whose last event is "tool"
and that has no live session was cut off by a crash or a reboot. Stdlib only, cheap to import: lanehook.py imports it
on every tool call of every WSL session. Override the location with FLEET_ROSTER (a directory).
"""
import json, os, re, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def native(p: str) -> str:
    """A path given in Windows form still works from WSL/Linux, and vice versa for /mnt paths on Windows."""
    if os.name != "nt":
        return re.sub(r"^([A-Za-z]):[\\/]", lambda m: f"/mnt/{m.group(1).lower()}/", p).replace("\\", "/")
    m = re.match(r"^/mnt/([a-z])/(.*)$", p)
    return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\") if m else p


def root() -> Path:
    return Path(native(os.environ["FLEET_ROSTER"])) if os.environ.get("FLEET_ROSTER") else HERE.parents[2] / "agent-fleet-lanes"


def _write(f: Path, d: dict) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def load() -> dict:
    d, out = root(), {}
    for f in (sorted(d.glob("*.json")) if d.is_dir() else []):
        try:
            out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return out


def record(name: str, **entry) -> bool:
    if not NAME_RE.fullmatch(name):
        return False
    _write(root() / f"{name}.json", {**entry, "spawned": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return True


def forget(names) -> None:
    for n in names:
        if NAME_RE.fullmatch(n):
            (root() / f"{n}.json").unlink(missing_ok=True)


def beat_path(name: str) -> Path:
    return root() / "heartbeat" / f"{name}.json"


def beat(name: str, event: str) -> None:
    _write(beat_path(name), {"last": event, "ts": time.time()})


def heartbeat(name: str) -> dict:
    try:
        return json.loads(beat_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
