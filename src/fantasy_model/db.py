"""DuckDB connection, raw landing, upserts."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from fantasy_model import config
from fantasy_model.schema import SCHEMA


def connect(path: Path | None = None, wait_s: float = 120.0) -> duckdb.DuckDBPyConnection:
    """Read-write connection. DuckDB allows one writer and no writer while another process holds
    the file (e.g. `ffm serve` mid-computation), so wait for the lock instead of failing a job."""
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_s
    while True:
        try:
            con = duckdb.connect(str(path))
            break
        except duckdb.IOException:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1.0)
    con.execute(SCHEMA)
    return con


def now_utc() -> datetime:
    return datetime.now(UTC)


def land_raw(
    con, snapshot_id: str, platform: str, endpoint: str, url: str, status: int | None, body
) -> None:
    """Store the untouched payload BEFORE parsing. A parser bug never loses a snapshot."""
    con.execute(
        "INSERT INTO raw_response VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            snapshot_id,
            now_utc(),
            platform,
            endpoint,
            url,
            status,
            json.dumps(body) if body is not None else None,
        ],
    )


def upsert(con, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = (
        f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})"
    )
    con.executemany(sql, [[_jsonify(r[c]) for c in cols] for r in rows])
    return len(rows)


def _jsonify(v):
    return json.dumps(v) if isinstance(v, dict | list) else v
