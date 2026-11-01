"""Backups of everything that can't be re-downloaded: the DuckDB file (snapshots of FAAB, pools,
projections and statuses at bid time), model files and the league config.

Consistency: we take DuckDB's write lock (db.connect waits for jobs or `ffm serve` to finish),
checkpoint so the file holds everything, and zip it while holding the lock. Each backup is then
verified by opening the copy before old ones are pruned. Point `[backup] dir` in
config/leagues.toml at a synced folder (e.g. OneDrive) so copies leave this machine.
"""

from __future__ import annotations

import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import duckdb

from fantasy_model import config, db

KEEP = 8  # weekly backups to keep
PREFIX = "fantasy-model-"


def backup_dir(cfg: dict | None = None) -> Path:
    cfg = cfg if cfg is not None else config.load_config()
    d = (cfg.get("backup") or {}).get("dir")
    return Path(d).expanduser() if d else Path.home() / "fantasy-model-backups"


def _files() -> list[tuple[Path, str]]:
    """(path on disk, name inside the zip) for everything worth keeping."""
    out = [(p, f"data/{p.name}") for p in sorted(config.DATA_DIR.glob("*")) if p.is_file()]
    if config.CONFIG_PATH.exists():
        out.append((config.CONFIG_PATH, "config/leagues.toml"))
    return out


def verify(archive: Path) -> int:
    """Open the database inside the backup; return its snapshot_run row count."""
    with zipfile.ZipFile(archive) as z, tempfile.TemporaryDirectory() as tmp:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"corrupt member in {archive.name}: {bad}")
        z.extract(f"data/{config.DB_PATH.name}", tmp)
        con = duckdb.connect(str(Path(tmp) / "data" / config.DB_PATH.name), read_only=True)
        try:
            return con.execute("SELECT count(*) FROM snapshot_run").fetchall()[0][0]
        finally:
            con.close()


def run(dest: Path | None = None, keep: int = KEEP) -> dict:
    dest = dest or backup_dir()
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / f"{PREFIX}{datetime.now():%Y-%m-%d_%H%M}.zip"
    con = db.connect()  # exclusive: nothing writes while we copy
    try:
        con.execute("CHECKPOINT")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for path, name in _files():
                z.write(path, name)
    finally:
        con.close()
    runs = verify(archive)
    old = sorted(dest.glob(f"{PREFIX}*.zip"))[:-keep] if keep else []
    for p in old:
        p.unlink()
    return {"archive": str(archive), "mb": round(archive.stat().st_size / 1e6, 1),
            "verified_runs": runs, "pruned": len(old)}  # fmt: skip
