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


def load_history(con, seasons: list[int]) -> dict[str, int]:
    """Weekly stats, snaps, schedules (spread/total -> implied team totals). Re-fetchable."""
    import nflreadpy as nfl

    loaders = {
        "nfl_player_stats": lambda: nfl.load_player_stats(seasons),
        "nfl_snap_counts": lambda: nfl.load_snap_counts(seasons),
        "nfl_schedules": lambda: nfl.load_schedules(seasons),
    }
    out = {}
    for table, fn in loaders.items():
        try:
            df = fn()
            con.register("_tmp", df.to_arrow())
            con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM _tmp")
            con.unregister("_tmp")
            out[table] = df.height
        except Exception as e:  # one source failing shouldn't block the others
            out[table] = f"FAILED: {e}"
    return out
