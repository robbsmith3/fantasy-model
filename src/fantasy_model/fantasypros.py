"""FantasyPros weekly consensus projections, via nflverse's daily scrape (load_ff_rankings).

Points only (`r2p_pts`; PPR pages for RB/WR/TE) and only for the upcoming week, so it can't be
backfilled: it must be snapshotted before each kickoff. Stored as stats {"pts_fp": points}.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import polars as pl

from fantasy_model.db import land_raw, upsert

PLATFORM = "fantasypros"
PAGES = {"qb": "QB", "ppr-rb": "RB", "ppr-wr": "WR", "ppr-te": "TE"}
ET = ZoneInfo("America/New_York")
TEAM_ALIASES = {"LAR": "LA", "JAC": "JAX"}  # FantasyPros -> nflverse schedule codes


def snapshot_projections(con, snapshot_id: str, season: int) -> int:
    import nflreadpy as nfl

    df = nfl.load_ff_rankings(type="week").filter(pl.col("page").is_in(list(PAGES)))
    land_raw(
        con, snapshot_id, PLATFORM, "ff_rankings.week", "nflreadpy:load_ff_rankings", 200,
        df.select("page", "fantasypros_id", "player_name", "team", "player_game_kickoff_ts",
                  "ecr", "sd", "r2p_pts").to_dicts(),
    )  # fmt: skip
    # (team, game date ET) -> week, from the schedule nflverse also publishes.
    week_of = {}
    for wk, gameday, home, away in con.execute(
        "SELECT week, gameday, home_team, away_team FROM nfl_schedules WHERE season = ?",
        [season],
    ).fetchall():
        week_of[(home, str(gameday))] = week_of[(away, str(gameday))] = wk
    rows = []
    for r in df.iter_rows(named=True):
        ts = r["player_game_kickoff_ts"]
        if not ts or r["r2p_pts"] is None:
            continue
        day = datetime.fromtimestamp(ts, tz=UTC).astimezone(ET).date().isoformat()
        wk = week_of.get((TEAM_ALIASES.get(r["team"], r["team"]), day))
        if wk is None:
            continue
        rows.append(
            {
                "snapshot_id": snapshot_id,
                "source": PLATFORM,
                "season": season,
                "week": wk,
                "source_player_id": str(r["fantasypros_id"]),
                "position": PAGES[r["page"]],
                "nfl_team": r["team"],
                "stats": {"pts_fp": r["r2p_pts"], "ecr": r["ecr"], "ecr_sd": r["sd"]},
            }
        )
    return upsert(con, "projection_snapshot", rows)
