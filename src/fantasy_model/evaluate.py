"""Projection accuracy against actual PPR (nflverse), per source and for the consensus.

Scope: fantasy-relevant player-weeks (any source >= RELEVANT pts). A projected player who didn't
play scores 0: missing "will he play" is real error. Uses each source's newest snapshot for the
week; backfilled past weeks were checked to be pre-game (DNP players kept ~6.7-pt projections)."""

from __future__ import annotations

import numpy as np
import polars as pl

from fantasy_model import value

RELEVANT = 5.0
POSITIONS = ("QB", "RB", "WR", "TE")


def actual_points(con, season: int, weeks: list[int]) -> pl.DataFrame:
    return value.frame(
        con,
        """SELECT m.sleeper_id AS player_id, s.week::BIGINT AS week, s.fantasy_points_ppr AS actual
           FROM nfl_player_stats s JOIN player_id_map m ON m.gsis_id = s.player_id
           WHERE s.season = ? AND s.season_type = 'REG' AND list_contains(?, s.week)""",
        [season, weeks],
    )


def projection_table(
    con, platform: str, league_id: str, season: int, weeks: list[int], extra=None
) -> pl.DataFrame:
    """Wide table: player_id, week, position, actual, one column per source, consensus.
    `extra` = {name: DataFrame(player_id, week, points)} adds candidate models to compare."""
    scoring = value.league_scoring(con, platform, league_id)
    wide = None
    for s in value.SOURCES:
        sp = value.source_points(con, scoring, s, weeks, season).select(
            "player_id", "week", "position", pl.col("points").alias(s)
        )
        if sp.is_empty():
            continue
        if wide is None:
            wide = sp
        else:
            wide = wide.join(sp, on=["player_id", "week"], how="full", coalesce=True)
            wide = wide.with_columns(pl.coalesce("position", "position_right").alias("position"))
            wide = wide.drop("position_right")
    srcs = [s for s in value.SOURCES if s in wide.columns]
    wide = wide.with_columns(pl.mean_horizontal(srcs).alias("consensus"))
    for name, df in (extra or {}).items():
        wide = wide.join(
            df.select("player_id", "week", pl.col("points").alias(name)),
            on=["player_id", "week"],
            how="left",
        )
    act = actual_points(con, season, weeks)
    wide = wide.join(act, on=["player_id", "week"], how="left").with_columns(
        pl.col("actual").fill_null(0.0)
    )
    return wide.filter(pl.col("position").is_in(POSITIONS) & (pl.max_horizontal(srcs) >= RELEVANT))


def accuracy(table: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
    """MAE, bias and mean within-(week, position) Spearman rho, on rows where every col exists."""
    t = table.drop_nulls(cols)
    out = []
    for pos in (*POSITIONS, "ALL"):
        d = t if pos == "ALL" else t.filter(pl.col("position") == pos)
        row = {"position": pos, "n": d.height}
        for c in cols:
            err = (d[c] - d["actual"]).to_numpy()
            rho = [
                g.select(pl.corr(c, "actual", method="spearman")).item()
                for _, g in d.group_by("week", "position")
                if g.height > 5
            ]
            row[f"mae_{c}"] = float(np.mean(np.abs(err))) if len(err) else None
            row[f"bias_{c}"] = float(np.mean(err)) if len(err) else None
            row[f"rho_{c}"] = float(np.nanmean(rho)) if rho else None
        out.append(row)
    return pl.DataFrame(out)


def model_backtest(
    con, platform: str, league_id: str, season: int, weeks: list[int]
) -> pl.DataFrame:
    """Walk-forward: our model trained only on weeks before each target week, compared with the
    sources and the equal-weight average of Sleeper + ESPN + model (the production consensus)."""
    from fantasy_model import projection_model as pm

    rows = pm.build_rows(con, list(range(season - pm.TRAIN_SEASONS + 1, season + 1)))
    preds = pm.walk_forward(rows, season, weeks)
    g2s = dict(
        con.execute(
            "SELECT gsis_id, sleeper_id FROM player_id_map"
            " WHERE gsis_id IS NOT NULL AND sleeper_id IS NOT NULL"
        ).fetchall()
    )
    own = (
        preds.with_columns(pl.col("player_id").replace_strict(g2s, default=None))
        .drop_nulls("player_id")
        .select("player_id", pl.col("week").cast(pl.Int64), "points")
    )
    table = projection_table(con, platform, league_id, season, weeks, extra={"model_wf": own})
    table = table.drop_nulls(["sleeper", "espn", "model_wf"]).with_columns(
        ((pl.col("sleeper") + pl.col("espn") + pl.col("model_wf")) / 3).alias("avg3")
    )
    return accuracy(table, ["sleeper", "espn", "model_wf", "avg3"])
