#!/usr/bin/env python3
"""Claude Code Stop hook: keep the LEAD agent working instead of idling mid-task.

Why: a lead told to "work continuously" still ends its turn after a status report such as
"I'm starting step 1 now." with no tool call, then sits idle until a human nudges it. A prose
order cannot keep a turn open; a Stop hook can (measured 2026-10-08 on a live lead).

Scope: acts only when the session's env has AGENT_NAME=lead (the agent-board lead convention)
or AGENT_KEEPALIVE=1. Every other session exits 0 at once. Off: AGENT_KEEPALIVE_OFF=1.

Allows the stop when the FINAL paragraph of the last assistant message asks a question ('?')
or names a wait (owner question, blocked, waiting on you / a background task). Only the final
paragraph counts: lead reports often carry a standing "still waiting on you: question #2"
status line mid-message, which must not excuse a stall.

Safety: at most MAX_BLOCKS consecutive blocks per session (counter in the temp dir), then one
stop is allowed and the counter resets. Fails open on any error. Stdlib only.

Install: python scripts/install_keepalive.py   (adds this hook to ~/.claude/settings.json)
"""
import json, os, re, sys, tempfile
from pathlib import Path

MAX_BLOCKS = 5
TAIL_BYTES = 1 << 20   # read only the transcript's last 1 MB (live file, can be many MB)
WAIT = re.compile(
    r"\?|owner question|waiting on you|waiting for (you|the owner)|blocked on|stop list|"
    r"need your (answer|approval|yes)|"
    r"waiting (on|for) (the |a )?(background|task|battery|scorer|codex|notification|teammate|ci|build|review)",
    re.I)
REASON = ("keepalive ({n}/{cap}): you are the lead on a continuous-work order. Your last message "
          "ended without an open question, so do the next step you named NOW with a tool call. "
          "End a turn only to ask the owner something (end the message with the question) or to "
          "wait on a named background task.")


def active(env=os.environ) -> bool:
    if env.get("AGENT_KEEPALIVE_OFF") == "1":
        return False
    return env.get("AGENT_NAME") == "lead" or env.get("AGENT_KEEPALIVE") == "1"


def last_text(hook: dict) -> str:
    text = hook.get("last_assistant_message") or ""
    if text.strip():
        return text
    tp = hook.get("transcript_path")
    if not tp or not Path(tp).is_file():
        return ""
    with open(tp, "rb") as f:
        f.seek(0, 2); size = f.tell(); f.seek(max(0, size - TAIL_BYTES))
        lines = f.read().decode("utf-8", "replace").split("\n")
    for line in reversed(lines[1:] if size > TAIL_BYTES else lines):   # first line may be cut
        if '"assistant"' not in line or '"text"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant":
            continue
        parts = [c.get("text", "") for c in (d.get("message") or {}).get("content") or []
                 if isinstance(c, dict) and c.get("type") == "text"]
        if "".join(parts).strip():
            return "\n".join(parts)
    return ""


def final_paragraph(text: str) -> str:
    paras = [p for p in re.split(r"\r?\n\s*\r?\n", text) if p.strip()]
    return paras[-1][-400:] if paras else ""


def decide(hook: dict, counter_dir: Path) -> dict | None:
    """Return the block decision, or None to allow the stop."""
    sid = re.sub(r"[^\w.-]", "_", str(hook.get("session_id") or ""))
    text = last_text(hook)
    if not sid or not text.strip():
        return None
    counter = counter_dir / f"agent-keepalive-{sid}.count"
    if WAIT.search(final_paragraph(text)):
        counter.unlink(missing_ok=True)
        return None
    n = int(counter.read_text() or 0) if counter.exists() else 0
    if n >= MAX_BLOCKS:
        counter.unlink(missing_ok=True)
        return None
    counter.write_text(str(n + 1))
    return {"decision": "block", "reason": REASON.format(n=n + 1, cap=MAX_BLOCKS)}


def main() -> int:
    try:
        if not active():
            return 0
        # bytes, not text: Windows decodes piped stdin as cp1252, which turns PowerShell 5.1's UTF-8 BOM into
        # "ï»¿" (never stripped, so json.loads failed and the hook failed open); hook input is UTF-8 by contract
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        if not raw.strip():
            return 0
        out = decide(json.loads(raw), Path(tempfile.gettempdir()))
        if out:
            print(json.dumps(out))
    except Exception:
        pass   # fail open, always
    return 0


if __name__ == "__main__":
    sys.exit(main())
