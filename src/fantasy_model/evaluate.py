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


def sim_validation(
    con, platform: str, league_id: str, season: int, weeks: list[int], sigma: float = 22.7
) -> dict:
    """Score chop predictions on past weeks with each team's actual starters and the pre-game
    consensus (Sleeper + ESPN). Compares team-level N(sum mu, sigma) with player-level sims.

    Returns per-week rows and summary: mean log-likelihood of the team actually chopped,
    Brier score over teams, and team-score calibration (z mean/sd, 80% interval coverage)."""
    import json

    from fantasy_model import player_sim

    scoring = value.league_scoring(con, platform, league_id)
    bank = player_sim.load_bank(con)
    rng = np.random.default_rng(1)
    per_week, zs, cover = [], [], []
    for w in weeks:
        tw = con.execute(
            """SELECT team_id, points, starters FROM team_week
               WHERE platform = ? AND league_id = ? AND season = ? AND week = ? AND points > 0""",
            [platform, league_id, season, w],
        ).fetchall()
        if len(tw) < 2:
            continue
        mus = []
        for src in ("sleeper", "espn"):
            mus.append(value.source_points(con, scoring, src, [w], season).select(
                "player_id", "position", "nfl_team", pl.col("points").alias(src)))  # fmt: skip
        proj = mus[0].join(mus[1].drop("position", "nfl_team"), on="player_id", how="left")
        proj = proj.with_columns(pl.mean_horizontal("sleeper", "espn").alias("mu"))
        lineups = {}
        for t, _, st in tw:
            st = json.loads(st) if isinstance(st, str) else st
            lineups[t] = [p for p in (st or []) if p and p != "0"]
        sims = player_sim.simulate_lineups(proj, lineups, bank, n_sims=20000, seed=w)
        teams = [t for t, _, _ in tw]
        actual = np.array([p for _, p, _ in tw])
        S = np.stack([sims[t] for t in teams], axis=1)  # (n_sims, teams)
        mu_team = S.mean(axis=0)
        p_player = np.bincount(S.argmin(axis=1), minlength=len(teams)) / S.shape[0]
        T = mu_team[None, :] + sigma * rng.standard_normal(S.shape)
        p_team = np.bincount(T.argmin(axis=1), minlength=len(teams)) / T.shape[0]
        chopped = int(actual.argmin())
        onehot = np.eye(len(teams))[chopped]
        sd = S.std(axis=0)
        zs.extend((actual - mu_team) / sd)
        lo, hi = np.quantile(S, 0.1, axis=0), np.quantile(S, 0.9, axis=0)
        cover.extend((actual >= lo) & (actual <= hi))
        per_week.append(
            {
                "week": w,
                "teams": len(teams),
                "chopped": teams[chopped],
                "p_player": float(p_player[chopped]),
                "p_team": float(p_team[chopped]),
                "rank_player": int((p_player > p_player[chopped]).sum()) + 1,
                "brier_player": float(((p_player - onehot) ** 2).sum()),
                "brier_team": float(((p_team - onehot) ** 2).sum()),
                "proj_bias": float((mu_team - actual).mean()),
                "team_sd": float(sd.mean()),
            }
        )
    df = pl.DataFrame(per_week)
    eps = 1e-3
    summary = {
        "loglik_player": float(np.log(df["p_player"].to_numpy() + eps).mean()),
        "loglik_team": float(np.log(df["p_team"].to_numpy() + eps).mean()),
        "brier_player": float(df["brier_player"].mean()),
        "brier_team": float(df["brier_team"].mean()),
        "z_mean": float(np.mean(zs)),
        "z_sd": float(np.std(zs)),
        "cover80": float(np.mean(cover)),
    }
    return {"weeks": df, "summary": summary}
