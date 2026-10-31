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


def projected_points(con, platform: str, league_id: str, weeks: list[int]) -> pl.DataFrame:
    """One row per (player, week) from the newest projection snapshot, scored for this league."""
    raw = con.execute(
        "SELECT scoring FROM league WHERE platform = ? AND league_id = ?", [platform, league_id]
    ).fetchall()
    if not raw:
        raise SystemExit(f"league {platform}:{league_id} not synced; run `ffm run pre_waiver`")
    scoring = json.loads(raw[0][0]) if isinstance(raw[0][0], str) else raw[0][0]
    snap = latest_snapshot(con, "projection_snapshot")
    rows = con.execute(
        """SELECT source_player_id, week, position, nfl_team, stats FROM projection_snapshot
           WHERE snapshot_id = ? AND list_contains(?, week) AND list_contains(?, position)""",
        [snap, weeks, list(ROSTER_POS)],
    ).fetchall()
    return pl.DataFrame(
        [
            {
                "player_id": pid,
                "week": wk,
                "position": pos,
                "nfl_team": team,
                "points": score(json.loads(st) if isinstance(st, str) else st, scoring),
            }
            for pid, wk, pos, team, st in rows
        ],
        schema={
            "player_id": pl.Utf8,
            "week": pl.Int64,
            "position": pl.Utf8,
            "nfl_team": pl.Utf8,
            "points": pl.Float64,
        },
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
