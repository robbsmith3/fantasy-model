"""recs/: the only place this tool writes, and only with --save. Every run empties it first."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

MAX_AGE_S = 24 * 3600


def purge(recs_dir: Path) -> int:
    """Delete every file in recs/. Returns how many."""
    if not recs_dir.exists():
        return 0
    n = 0
    for f in recs_dir.iterdir():
        if f.is_file():
            f.unlink()
            n += 1
    return n


def clean_at_start(recs_dir: Path, now: float | None = None) -> int:
    """Start of every run: files older than 24 hours go, and then everything else too (the spec
    clears the folder each run), so no saved output outlives the next run."""
    now = now or time.time()
    old = [f for f in recs_dir.iterdir() if f.is_file() and now - f.stat().st_mtime > MAX_AGE_S] \
        if recs_dir.exists() else []  # fmt: skip
    for f in old:
        f.unlink()
    return len(old) + purge(recs_dir)


def save(recs_dir: Path, text: str) -> Path:
    recs_dir.mkdir(mode=0o700, exist_ok=True)
    path = recs_dir / f"recs-{datetime.now():%Y%m%d-%H%M}.txt"
    path.write_text(text)
    path.chmod(0o600)
    return path
