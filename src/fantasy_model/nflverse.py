"""nflverse loaders: cross-platform player ID map + re-fetchable history (for Week B variance)."""

from __future__ import annotations

import polars as pl

ID_COLS = [
    "gsis_id",
    "sleeper_id",
    "yahoo_id",
    "espn_id",
    "fantasypros_id",
    "name",
    "position",
    "team",
]


def _clean_ids(df: pl.DataFrame) -> pl.DataFrame:
    cols = [c for c in ID_COLS if c in df.columns]
    return df.select(
        [pl.col(c).cast(pl.Utf8).str.replace(r"\.0$", "").alias(c) for c in cols]
        + [pl.lit(None, pl.Utf8).alias(c) for c in ID_COLS if c not in df.columns]
    ).select(ID_COLS)


def load_id_map(con) -> int:
    import nflreadpy as nfl

    df = _clean_ids(nfl.load_ff_playerids())
    con.execute("DELETE FROM player_id_map")
    con.register("_ids", df.to_arrow())
    con.execute("INSERT INTO player_id_map SELECT * FROM _ids")
    con.unregister("_ids")
    return df.height


def _replace_seasons(con, table: str, df: pl.DataFrame, seasons: list[int]) -> None:
    """Swap in `seasons` rows only; other seasons stay. Atomic, and tolerates new columns."""
    con.register("_tmp", df.to_arrow())
    try:
        con.execute("BEGIN")
        con.execute(f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM _tmp LIMIT 0")
        have = {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}
        for col, typ, *_ in con.execute("DESCRIBE _tmp").fetchall():
            if col not in have:  # nflverse adds columns over time; older seasons get NULL
                con.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" {typ}')
        con.execute(f"DELETE FROM {table} WHERE season IN (SELECT unnest(?))", [seasons])
        con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _tmp")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.unregister("_tmp")


def load_history(con, seasons: list[int]) -> dict[str, int]:
    """Weekly stats, snaps, schedules (spread/total -> implied team totals). Re-fetchable.

    Only the given seasons are replaced, so `ffm history 2026` refreshes the current year alone.
    """
    import nflreadpy as nfl

    loaders = {
        "nfl_player_stats": lambda: nfl.load_player_stats(seasons),
        "nfl_snap_counts": lambda: nfl.load_snap_counts(seasons),
        "nfl_schedules": lambda: nfl.load_schedules(seasons),
        # ffverse expected fantasy points from usage (open-source model); key input for our own
        # projections since usage predicts future scoring better than realized points.
        # (it ships season as text and week as float; normalize to match the other tables)
        # Official weekly injury reports (filed Wed-Fri, before the game).
        "nfl_injuries": lambda: nfl.load_injuries(seasons=seasons),
        "nfl_ff_opportunity": lambda: nfl.load_ff_opportunity(seasons=seasons).with_columns(
            pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
        ),
    }
    out = {}
    for table, fn in loaders.items():
        try:
            df = fn()
            _replace_seasons(con, table, df, seasons)
            out[table] = df.height
        except Exception as e:  # one source failing shouldn't block the others
            out[table] = f"FAILED: {e}"
    return out
