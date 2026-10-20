#!/usr/bin/env python3
"""board.py with cached reads: the same commands, arguments and output, run through fastboard.

Every post and claim re-reads the whole board to find the next sequence number; from a WSL lane on
a Windows-drive board that read took 11.3 s per call (2026-10-19, 4,315 messages) and 52 ms through
the cache. Lanes and briefs call this file; board.py stays byte-identical because score.py pins its
hash for open tasks, and scoring keeps the uncached loader.

  python fboard.py --board B post --as G3 "..."      (any board.py command)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board  # noqa: E402
import fastboard  # noqa: E402

fastboard.install()

if __name__ == "__main__":
    sys.exit(board.main())
