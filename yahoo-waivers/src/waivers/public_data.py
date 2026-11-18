"""Public nflverse data (not Yahoo data), cached locally, turned into per-player signals.

Loading goes through nflreadpy with its filesystem cache in ~/.cache/yahoo-waivers. Everything
after loading is pure functions on polars frames, so tests feed synthetic tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from waivers.yahoo_parse import STAT_COLUMNS

CACHE_DIR = Path.home() / ".cache" / "yahoo-waivers"
RECENT = 3  # games in the "recent" window
YAHOO_TEAM = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX"}  # Yahoo abbreviation -> nflverse


@dataclass
class PublicData:
    stats: pl.DataFrame  # weekly player stats (player_id = gsis id)
    snaps: pl.DataFrame  # snap counts (pfr_player_id, offense_pct)
    redzone: pl.DataFrame  # player_id, week, rz (targets + carries inside the 20)
    ids: pl.DataFrame  # yahoo_id, gsis_id, pfr_id
    schedule: pl.DataFrame  # week, home_team, away_team
    injuries: pl.DataFrame  # gsis_id, week, report_status


@dataclass
class Signals:
    gsis_id: str
    team: str | None
    games: int
    ppg: float
    ppg_recent: float
    snap: float | None
    snap_recent: float | None
    opps: float  # targets + carries per game
    opps_recent: float
    rz_recent: float  # red-zone targets + carries per game, recent window
    report: str | None  # latest public injury report status


def load(season: int) -> PublicData:  # pragma: no cover - network; tests use synthetic tables
    import nflreadpy as nfl

    nfl.update_config(cache_mode="filesystem", cache_dir=CACHE_DIR / "nflverse",
                      cache_duration=12 * 3600)  # fmt: skip
    stats = nfl.load_player_stats([season]).filter(pl.col("season_type") == "REG")
    pbp = nfl.load_pbp([season]).filter(
        (pl.col("season_type") == "REG") & (pl.col("yardline_100") <= 20)
    )
    rz = (
        pl.concat(
            [
                pbp.select("week", pl.col("rusher_player_id").alias("player_id")),
                pbp.select("week", pl.col("receiver_player_id").alias("player_id")),
            ]
        )
        .drop_nulls("player_id")
        .group_by("player_id", "week")
        .agg(pl.len().alias("rz"))
    )
    ids = nfl.load_ff_playerids().select("yahoo_id", "gsis_id", "pfr_id")
    sched = nfl.load_schedules([season]).filter(pl.col("game_type") == "REG")
    try:
        injuries = nfl.load_injuries([season])
    except Exception:  # published late in the week; fine to go without
        injuries = pl.DataFrame({"gsis_id": [], "week": [], "report_status": []})
    return PublicData(stats, nfl.load_snap_counts([season]), rz, ids,
                      sched.select("week", "home_team", "away_team"),
                      injuries.select("gsis_id", "week", "report_status"))  # fmt: skip


def _id(v) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s[:-2] if s.endswith(".0") else s


def points(stats: pl.DataFrame, scoring: dict[int, float]) -> pl.Series:
    """Fantasy points under the league's scoring, from nflverse columns. Falls back to PPR."""
    terms = []
    for stat_id, value in scoring.items():
        cols = [c for c in STAT_COLUMNS.get(stat_id, ()) if c in stats.columns]
        if cols:
            terms.append(pl.sum_horizontal([pl.col(c).fill_null(0) for c in cols]) * value)
    if not terms:
        return stats["fantasy_points_ppr"].fill_null(0.0)
    return stats.select(pl.sum_horizontal(terms).alias("pts"))["pts"]


def signals(data: PublicData, scoring: dict[int, float], before_week: int) -> dict[str, Signals]:
    """{yahoo_id: Signals} from games before `before_week`."""
    st = data.stats.filter(pl.col("week") < before_week)
    st = st.with_columns(points(st, scoring).alias("pts"),
                         (pl.col("targets").fill_null(0) + pl.col("carries").fill_null(0))
                         .alias("opp"))  # fmt: skip
    ids = data.ids.with_columns(
        pl.col("yahoo_id").map_elements(_id, return_dtype=pl.Utf8),
        pl.col("gsis_id").cast(pl.Utf8),
        pl.col("pfr_id").cast(pl.Utf8),
    ).drop_nulls(["yahoo_id", "gsis_id"])
    pfr = dict(ids.select("gsis_id", "pfr_id").iter_rows())
    snaps = data.snaps.filter(pl.col("week") < before_week)
    rz = data.redzone.filter(pl.col("week") < before_week)
    inj = data.injuries.filter(pl.col("week") <= before_week).sort("week")
    report = dict(inj.select(pl.col("gsis_id").cast(pl.Utf8), "report_status").iter_rows())
    out = {}
    by_player = {k[0]: g.sort("week") for k, g in st.group_by("player_id")}
    for yid, gsis in ids.select("yahoo_id", "gsis_id").iter_rows():
        g = by_player.get(gsis)
        if g is None or g.is_empty():
            continue
        recent = g.tail(RECENT)
        weeks = set(recent["week"].to_list())
        sn = snaps.filter(pl.col("pfr_player_id") == pfr.get(gsis))
        rzp = rz.filter((pl.col("player_id") == gsis) & pl.col("week").is_in(list(weeks)))
        out[yid] = Signals(
            gsis_id=gsis,
            team=g["team"][-1] if "team" in g.columns else None,
            games=g.height,
            ppg=float(g["pts"].mean()),
            ppg_recent=float(recent["pts"].mean()),
            snap=float(sn["offense_pct"].mean()) if sn.height else None,
            snap_recent=(
                float(sn.filter(pl.col("week").is_in(list(weeks)))["offense_pct"].mean())
                if sn.height
                else None
            ),  # fmt: skip
            opps=float(g["opp"].mean()),
            opps_recent=float(recent["opp"].mean()),
            rz_recent=float(rzp["rz"].sum()) / max(len(weeks), 1),
            report=report.get(gsis),
        )
    return out


def team_code(abbr: str | None) -> str | None:
    if not abbr:
        return None
    return YAHOO_TEAM.get(abbr.upper(), abbr.upper())


def byes(schedule: pl.DataFrame) -> dict[str, int]:
    """{team: bye week}: the first week from 1 to the last scheduled week it doesn't play."""
    weeks = sorted(set(schedule["week"].to_list()))
    teams = set(schedule["home_team"]) | set(schedule["away_team"])
    out = {}
    for t in teams:
        played = set(
            schedule.filter((pl.col("home_team") == t) | (pl.col("away_team") == t))["week"]
        )
        missing = [w for w in weeks if w not in played]
        if missing:
            out[t] = missing[0]
    return out


def opponents(schedule: pl.DataFrame, week: int) -> dict[str, str]:
    g = schedule.filter(pl.col("week") == week)
    return dict(zip(g["home_team"], g["away_team"], strict=True)) | dict(
        zip(g["away_team"], g["home_team"], strict=True)
    )


def matchup_factors(data: PublicData, scoring: dict[int, float], before_week: int) -> dict:
    """{(defense, position): points allowed vs league average}, shrunk toward 1 early on."""
    st = data.stats.filter(pl.col("week") < before_week)
    if st.is_empty() or "opponent_team" not in st.columns:
        return {}
    st = st.with_columns(points(st, scoring).alias("pts"))
    per_game = (
        st.group_by("opponent_team", "position", "week").agg(pl.col("pts").sum())
        .group_by("opponent_team", "position").agg(pl.col("pts").mean(), pl.len().alias("n"))
    )  # fmt: skip
    avg = dict(per_game.group_by("position").agg(pl.col("pts").mean()).iter_rows())
    out = {}
    for d, pos, pts, n in per_game.iter_rows():
        if avg.get(pos):
            raw = pts / avg[pos]
            out[(d, pos)] = 1 + (raw - 1) * n / (n + 4)  # 4 games of "average" as a prior
    return out
