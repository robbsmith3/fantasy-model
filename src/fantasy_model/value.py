"""Player value: score projected stat lines with a league's own rules (design rule 3: stats, not
points), then sum over the weeks that matter for the next waiver run."""

from __future__ import annotations

import json

import polars as pl

# Positions a player can fill in our leagues' lineups (no K; FB/IDP never start).
ROSTER_POS = ("QB", "RB", "WR", "TE", "DEF")


def score(stats: dict, scoring: dict) -> float:
    """Fantasy points for one stat line. Unscored stats (pts_ppr, adp, yds_allow...) are ignored;
    DEF points-allowed buckets work because projections carry the bucket as a 0/1 stat."""
    return round(sum(v * scoring[k] for k, v in stats.items() if k in scoring and v), 2)


def frame(con, sql: str, params: list | None = None) -> pl.DataFrame:
    return pl.from_arrow(con.sql(sql, params=params or []).to_arrow_table())


def latest_snapshot(con, table: str, where: str = "TRUE") -> str | None:
    return con.execute(f"SELECT max(snapshot_id) FROM {table} WHERE {where}").fetchall()[0][0]


# Consensus: each source scored on its own, then an equal-weight mean per player-week. Equal
# weights because forecast-combination research finds them hard to beat with few, noisy sources;
# revisit once `ffm eval-projections` has enough weeks to justify estimated weights. Backtest
# 2026 wks 3-7: equal-weight Sleeper+ESPN+model MAE 5.389 vs Sleeper 5.427; fitted weights 5.391.
SOURCES = ("sleeper", "espn", "fantasypros", "model")
POINTS_KEY = {"fantasypros": "pts_fp", "model": "pts"}  # points-only sources
ID_COLUMN = {"espn": "espn_id", "fantasypros": "fantasypros_id"}  # player_id_map column


def league_scoring(con, platform: str, league_id: str) -> dict:
    raw = con.execute(
        "SELECT scoring FROM league WHERE platform = ? AND league_id = ?", [platform, league_id]
    ).fetchall()
    if not raw:
        raise SystemExit(f"league {platform}:{league_id} not synced; run `ffm run pre_waiver`")
    return json.loads(raw[0][0]) if isinstance(raw[0][0], str) else raw[0][0]


def source_points(
    con, scoring: dict, source: str, weeks: list[int], season: int | None = None,
    before: str | None = None,
) -> pl.DataFrame:  # fmt: skip
    """One source's points per (sleeper player_id, week), from that source's newest snapshot
    covering each week (optionally only snapshots with snapshot_id < `before`, for backtests)."""
    rows = con.execute(
        f"""WITH snaps AS (
                SELECT week, max(snapshot_id) AS snap FROM projection_snapshot
                WHERE source = ? AND list_contains(?, week)
                  {"AND season = ?" if season else ""} {"AND snapshot_id < ?" if before else ""}
                GROUP BY week)
            SELECT p.source_player_id, p.week, p.position, p.nfl_team, p.stats
            FROM projection_snapshot p JOIN snaps s ON p.week = s.week AND p.snapshot_id = s.snap
            WHERE p.source = ? AND list_contains(?, p.position)""",
        [source, weeks, *([season] if season else []), *([before] if before else []),
         source, list(ROSTER_POS)],
    ).fetchall()  # fmt: skip
    id_map = {}
    if source in ID_COLUMN:
        col = ID_COLUMN[source]
        id_map = dict(
            con.execute(
                f"SELECT {col}, sleeper_id FROM player_id_map"
                f" WHERE {col} IS NOT NULL AND sleeper_id IS NOT NULL"
            ).fetchall()
        )
    out = []
    for pid, wk, pos, team, st in rows:
        st = json.loads(st) if isinstance(st, str) else st
        if id_map:
            pid = id_map.get(pid)
            if pid is None:
                continue
        pts = st.get(POINTS_KEY[source]) if source in POINTS_KEY else score(st, scoring)
        if pts is not None:
            out.append((pid, wk, pos, team, float(pts)))
    return pl.DataFrame(
        out,
        schema={"player_id": pl.Utf8, "week": pl.Int64, "position": pl.Utf8,
                "nfl_team": pl.Utf8, "points": pl.Float64},
        orient="row",
    )  # fmt: skip


def projected_points(
    con, platform: str, league_id: str, weeks: list[int], sources=SOURCES,
    season: int | None = None, before: str | None = None,
) -> pl.DataFrame:  # fmt: skip
    """Consensus points per (player, week): equal-weight mean of the sources that project the
    player that week. Also n_sources and src_sd (disagreement; a cheap uncertainty signal).
    Position/team come from Sleeper when it has the player (our canonical ids)."""
    scoring = league_scoring(con, platform, league_id)
    parts = [
        source_points(con, scoring, s, weeks, season, before).with_columns(
            pl.lit(s).alias("source")
        )
        for s in sources
    ]
    long = pl.concat(parts)
    meta = long.sort(pl.col("source") != "sleeper").unique(["player_id", "week"], keep="first")
    agg = long.group_by("player_id", "week").agg(
        pl.col("points").mean().round(2).alias("points"),
        pl.len().alias("n_sources"),
        pl.col("points").std().fill_null(0.0).round(2).alias("src_sd"),
    )
    return agg.join(
        meta.select("player_id", "week", "position", "nfl_team"), on=["player_id", "week"]
    )


def player_values(con, platform: str, league_id: str, weeks: list[int]) -> pl.DataFrame:
    """Rest-of-horizon value per player, with who rosters them now (None = available).

    Columns: player_id, full_name, position, nfl_team, injury_status, owner_team_id,
    pts_w<N> for each week, ros_pts. Players with no projection in a week (bye) score 0.
    """
    pts = projected_points(con, platform, league_id, weeks)
    wide = (
        pts.pivot(on="week", index="player_id", values="points", aggregate_function="first")
        .rename({str(w): f"pts_w{w}" for w in weeks if str(w) in pts["week"].cast(pl.Utf8)})
        .fill_null(0.0)
    )
    for w in weeks:
        if f"pts_w{w}" not in wide.columns:
            wide = wide.with_columns(pl.lit(0.0).alias(f"pts_w{w}"))
    wide = wide.with_columns(pl.sum_horizontal([f"pts_w{w}" for w in weeks]).alias("ros_pts"))

    status_snap = latest_snapshot(con, "player_status_snapshot")
    meta = frame(
        con,
        """SELECT player_id, full_name, position, nfl_team, injury_status
           FROM player_status_snapshot WHERE snapshot_id = ?""",
        [status_snap],
    )
    roster_snap = latest_snapshot(
        con, "roster_snapshot_canon", f"platform = '{platform}' AND league_id = '{league_id}'"
    )
    owners = frame(
        con,
        """SELECT sleeper_id AS player_id, team_id AS owner_team_id FROM roster_snapshot_canon
           WHERE snapshot_id = ? AND platform = ? AND league_id = ?""",
        [roster_snap, platform, league_id],
    )
    pos_team = pts.select("player_id", "position", "nfl_team").unique("player_id")
    return (
        wide.join(pos_team, on="player_id", how="left")
        .join(
            meta.select("player_id", "full_name", "injury_status"),
            on="player_id",
            how="left",
        )
        .join(owners, on="player_id", how="left")
        .select(
            "player_id",
            "full_name",
            "position",
            "nfl_team",
            "injury_status",
            "owner_team_id",
            *[f"pts_w{w}" for w in weeks],
            "ros_pts",
        )
        .sort("ros_pts", descending=True)
    )
