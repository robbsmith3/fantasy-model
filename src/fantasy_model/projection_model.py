"""Our own weekly PPR projection from open nflverse data (usage, expected points, Vegas lines).

Rows are (candidate player, target week): anyone who played in the previous LOOKBACK weeks of
games. Target = actual PPR, 0 if he didn't play, so the model also learns "will he play" (the
Princeton 2026 thesis found much of an ML edge over ESPN comes from that). Every feature uses
only games strictly before the target week; Vegas lines for the target game are pre-game info.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fantasy_model.value import frame

POSITIONS = ("QB", "RB", "WR", "TE")
LOOKBACK = 4  # weeks; candidate pool for a target week
REG_WEEKS = 18
TRAIN_SEASONS = 9  # 2018-2026 when forecasting 2026

USAGE = {
    "ppr": "fantasy_points_ppr",
    "xfp": "total_fantasy_points_exp",
    "tgt": "targets",
    "car": "carries",
    "tsh": "target_share",
    "aysh": "air_yards_share",
    "wopr": "wopr",
    "patt": "attempts",
}
FEATURES = [
    *[f"{k}_{w}" for k in USAGE for w in ("l1", "l3", "l8")],
    "ppr_ewm",
    "xfp_ewm",
    "games",
    "games_season",
    "weeks_since",
    "prior_ppg",
    "pos_code",
    "implied",
    "spread",
    "home",
    "opp_allowed",
    "week",
]
# Official injury report for the target week. Filed Wed-Fri, so only usable for in-week
# projections; at Tuesday waivers next week's report doesn't exist yet (use FEATURES alone).
INJURY_FEATURES = ["inj_report", "inj_practice"]
REPORT_CODE = {"Questionable": 1, "Doubtful": 2, "Out": 3}
PRACTICE_CODE = {
    "Full Participation in Practice": 0,
    "Limited Participation in Practice": 1,
    "Did Not Participate In Practice": 2,
}


def _gw(season, week):
    return season * 20 + week  # global week index; gaps across seasons are harmless


def player_games(con, seasons: list[int]) -> pl.DataFrame:
    """Regular-season games actually played, with usage and expected points."""
    return frame(
        con,
        """SELECT s.player_id, s.position, s.season, s.week::INT AS week, s.team,
                  s.opponent_team, s.fantasy_points_ppr, s.targets, s.carries, s.target_share,
                  s.air_yards_share, s.wopr, s.attempts, o.total_fantasy_points_exp
           FROM nfl_player_stats s
           LEFT JOIN nfl_ff_opportunity o
             ON o.player_id = s.player_id AND o.season = s.season AND o.week = s.week
           WHERE s.season_type = 'REG' AND list_contains(?, s.season)
             AND list_contains(?, s.position)""",
        [seasons, list(POSITIONS)],
    ).with_columns((pl.col("season") * 20 + pl.col("week")).alias("gw"))


def team_games(con, seasons: list[int]) -> pl.DataFrame:
    """One row per (team, season, week) with opponent, home flag, implied total and spread."""
    sched = frame(
        con,
        """SELECT season, week::INT AS week, home_team, away_team, total_line, spread_line
           FROM nfl_schedules WHERE game_type = 'REG' AND list_contains(?, season)""",
        [seasons],
    )
    # nflverse spread_line > 0 means the home team is favored by that many points.
    home = sched.select(
        "season", "week", pl.col("home_team").alias("team"), pl.col("away_team").alias("opp"),
        pl.lit(1).alias("home"),
        ((pl.col("total_line") + pl.col("spread_line")) / 2).alias("implied"),
        pl.col("spread_line").alias("spread"),
    )  # fmt: skip
    away = sched.select(
        "season", "week", pl.col("away_team").alias("team"), pl.col("home_team").alias("opp"),
        pl.lit(0).alias("home"),
        ((pl.col("total_line") - pl.col("spread_line")) / 2).alias("implied"),
        (-pl.col("spread_line")).alias("spread"),
    )  # fmt: skip
    return pl.concat([home, away]).with_columns(
        (pl.col("season") * 20 + pl.col("week")).alias("gw")
    )


def build_rows(con, seasons: list[int], as_of: tuple[int, int] | None = None) -> pl.DataFrame:
    """Candidate (player, target week) rows with features and target (`actual`).

    as_of=(season, week): only games before that week are known (drops a partly played week),
    and target weeks at or after it are featurized as of then, for forward projections."""
    games = player_games(con, seasons).sort("player_id", "gw")
    as_of_gw = _gw(*as_of) if as_of else None
    if as_of_gw is not None:
        games = games.filter(pl.col("gw") < as_of_gw)
    tg = team_games(con, seasons)
    # Lines are only posted ~1 week out; later weeks get the team's season-average line so far.
    tg = tg.with_columns(
        pl.col(c).fill_null(pl.col(c).mean().over("team", "season")) for c in ("implied", "spread")
    )

    # Points each defense allowed to each position per game, as of before each week.
    allowed = (
        games.group_by("opponent_team", "position", "season", "week", "gw")
        .agg(pl.col("fantasy_points_ppr").sum().alias("pts"))
        .sort("gw")
    )

    weeks = sorted({(s, w) for s, w in tg.select("season", "week").unique().iter_rows()})
    prior_ppg = (
        games.group_by("player_id", "season")
        .agg(pl.col("fantasy_points_ppr").mean().alias("ppg"))
        .with_columns((pl.col("season") + 1).alias("season"))
    )
    out = []
    for season, week in weeks:
        if week > REG_WEEKS:
            continue
        gw = _gw(season, week)
        hist = games.filter(pl.col("gw") < gw)
        ref = min(gw, as_of_gw) if as_of_gw is not None else gw  # "now" for future targets
        ref_week = ref - season * 20
        # Early weeks look back into last season; skip the unused gw slots between seasons.
        lo = ref - LOOKBACK - (20 - REG_WEEKS if ref_week <= LOOKBACK else 0)
        recent = hist.filter(pl.col("gw") >= lo)
        cand = recent.group_by("player_id").agg(pl.col("position").last(), pl.col("team").last())
        if cand.is_empty():
            continue
        h = hist.join(cand.select("player_id"), on="player_id")
        feats = h.group_by("player_id").agg(
            *[
                pl.col(src).tail(n).mean().alias(f"{k}_{tag}")
                for k, src in USAGE.items()
                for tag, n in (("l1", 1), ("l3", 3), ("l8", 8))
            ],
            pl.col("fantasy_points_ppr").ewm_mean(half_life=4).last().alias("ppr_ewm"),
            pl.col("total_fantasy_points_exp").ewm_mean(half_life=4).last().alias("xfp_ewm"),
            pl.len().alias("games"),
            (pl.col("season") == season).sum().alias("games_season"),
            (ref - pl.col("gw").max()).alias("weeks_since"),
        )
        rows = (
            cand.join(feats, on="player_id")
            .join(
                tg.filter((pl.col("season") == season) & (pl.col("week") == week)).select(
                    "team", "opp", "home", "implied", "spread"
                ),
                on="team",
                how="inner",  # no game (bye) -> no row
            )
            .with_columns(pl.lit(season).alias("season"), pl.lit(week).alias("week"))
        )
        opp = (
            allowed.filter(pl.col("gw") < gw)
            .group_by("opponent_team", "position")
            .agg(pl.col("pts").tail(8).mean().alias("opp_allowed"))
        )
        rows = rows.join(
            opp, left_on=["opp", "position"], right_on=["opponent_team", "position"], how="left"
        )
        out.append(rows)
    rows = pl.concat(out, how="diagonal_relaxed")
    actual = games.select(
        "player_id", "season", "week", pl.col("fantasy_points_ppr").alias("actual")
    )
    inj = frame(
        con,
        """SELECT gsis_id AS player_id, season::INT AS season, week::INT AS week,
                  report_status, practice_status
           FROM nfl_injuries WHERE season_type = 'REG' AND list_contains(?, season)""",
        [seasons],
    ).unique(["player_id", "season", "week"])
    inj = inj.select(
        "player_id",
        "season",
        "week",
        pl.col("report_status").replace_strict(REPORT_CODE, default=0).alias("inj_report"),
        pl.col("practice_status").replace_strict(PRACTICE_CODE, default=0).alias("inj_practice"),
    )
    return (
        rows.join(prior_ppg.rename({"ppg": "prior_ppg"}), on=["player_id", "season"], how="left")
        .join(inj, on=["player_id", "season", "week"], how="left")
        .with_columns(pl.col("inj_report").fill_null(0), pl.col("inj_practice").fill_null(0))
        .join(actual, on=["player_id", "season", "week"], how="left")
        .with_columns(
            pl.col("actual").fill_null(0.0),
            pl.col("position")
            .replace_strict({p: i for i, p in enumerate(POSITIONS)})
            .alias("pos_code"),
        )
    )


def fit(train: pl.DataFrame, features: list[str] = FEATURES, seed: int = 0):
    import lightgbm as lgb

    model = lgb.LGBMRegressor(
        n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=50,
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
        random_state=seed, verbose=-1,
    )  # fmt: skip
    model.fit(train.select(features).to_numpy(), train["actual"].to_numpy())
    model.features_ = features
    return model


def predict(model, rows: pl.DataFrame) -> np.ndarray:
    return np.clip(model.predict(rows.select(model.features_).to_numpy()), 0, None)


def walk_forward(
    rows: pl.DataFrame, season: int, weeks: list[int], features: list[str] = FEATURES
) -> pl.DataFrame:
    """For each target week, train on everything strictly earlier, predict that week."""
    preds = []
    for w in weeks:
        key = pl.col("season") * 20 + pl.col("week")
        train = rows.filter(key < season * 20 + w)
        test = rows.filter((pl.col("season") == season) & (pl.col("week") == w))
        m = fit(train, features)
        preds.append(test.select("player_id", "season", "week").with_columns(
            pl.Series("points", predict(m, test))
        ))  # fmt: skip
    return pl.concat(preds)


def complete_through(con, season: int) -> int:
    """First week of `season` with an unfinished game (features/training stop before it)."""
    wk = con.execute(
        """SELECT min(week) FROM nfl_schedules
           WHERE season = ? AND game_type = 'REG' AND result IS NULL""",
        [season],
    ).fetchall()[0][0]
    return int(wk) if wk is not None else REG_WEEKS + 1


def snapshot_projections(con, snapshot_id: str, season: int, weeks: list[int]) -> int:
    """Train on every completed week, project `weeks`, store as source 'model' (Sleeper ids)."""
    from fantasy_model.db import upsert

    as_of = (season, complete_through(con, season))
    rows = build_rows(con, list(range(season - (TRAIN_SEASONS - 1), season + 1)), as_of)
    key = pl.col("season") * 20 + pl.col("week")
    model = fit(rows.filter(key < _gw(*as_of)))
    target = rows.filter((pl.col("season") == season) & pl.col("week").is_in(weeks))
    g2s = dict(
        con.execute(
            "SELECT gsis_id, sleeper_id FROM player_id_map"
            " WHERE gsis_id IS NOT NULL AND sleeper_id IS NOT NULL"
        ).fetchall()
    )
    out = []
    for r, pts in zip(target.iter_rows(named=True), predict(model, target), strict=True):
        sid = g2s.get(r["player_id"])
        if sid:
            out.append(
                {
                    "snapshot_id": snapshot_id,
                    "source": "model",
                    "season": season,
                    "week": r["week"],
                    "source_player_id": sid,
                    "position": r["position"],
                    "nfl_team": r["team"],
                    "stats": {"pts": round(float(pts), 2)},
                }
            )
    return upsert(con, "projection_snapshot", out)
