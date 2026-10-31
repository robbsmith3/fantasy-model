"""Opponent bidding: reconstruct every past waiver claim with what the bidder could see, then model
what each opponent will bid. Sleeper returns losing (failed) claims too, so we see real demand.

Timeline (verified on the 2026 Sleeper league): week w's lowest team is chopped Monday night,
its roster hits waivers, and the main run processes Wednesday ~2am under leg w. So leg-w claims
were made knowing weeks 1..w."""

from __future__ import annotations

import math

import numpy as np
import polars as pl

from fantasy_model.value import frame


def weekly_ppr(con, season: int) -> pl.DataFrame:
    """Actual PPR points per (sleeper_id, week) from nflverse; matches our Sleeper scoring for
    offense (4pt pass TD, -2 INT). DEF isn't in nflverse player stats."""
    return frame(
        con,
        """SELECT m.sleeper_id AS player_id, s.week, s.fantasy_points_ppr AS pts
           FROM nfl_player_stats s JOIN player_id_map m ON m.gsis_id = s.player_id
           WHERE s.season = ? AND s.season_type = 'REG' AND m.sleeper_id IS NOT NULL""",
        [season],
    )


def bid_table(con, platform: str, league_id: str, season: int) -> pl.DataFrame:
    """One row per waiver claim: team, leg, player, bid, won, the bidder's FAAB before the run,
    and the player's scoring through that leg (ppg, last-3 avg, games)."""
    budget = con.execute(
        "SELECT faab_budget FROM league WHERE platform = ? AND league_id = ?", [platform, league_id]
    ).fetchall()[0][0]
    claims = frame(
        con,
        """SELECT t.transaction_id, t.week AS leg, t.team_id, t.status = 'complete' AS won,
                  coalesce(t.bid, 0) AS bid, t.processed_at, p.player_id,
                  coalesce(s.position, 'DEF') AS position
           FROM txn t
           JOIN txn_player p USING (platform, league_id, transaction_id)
           LEFT JOIN (SELECT DISTINCT ON (player_id) player_id, position
                      FROM player_status_snapshot ORDER BY snapshot_id DESC) s
             ON s.player_id = p.player_id
           WHERE t.platform = ? AND t.league_id = ? AND t.season = ?
             AND t.type = 'waiver' AND p.action = 'add'""",
        [platform, league_id, season],
    ).sort("processed_at")

    # FAAB before each claim = budget - winning bids processed strictly earlier.
    spent = claims.filter(pl.col("won")).select("team_id", "processed_at", "bid")
    claims = claims.with_columns(
        pl.struct("team_id", "processed_at")
        .map_elements(
            lambda r: (
                budget
                - spent.filter(
                    (pl.col("team_id") == r["team_id"])
                    & (pl.col("processed_at") < r["processed_at"])
                )["bid"].sum()
            ),
            return_dtype=pl.Int64,
        )
        .alias("faab_before")
    )

    pts = weekly_ppr(con, season)
    prior = dict(
        weekly_ppr(con, season - 1).group_by("player_id").agg(pl.col("pts").mean()).iter_rows()
    )
    form = []
    for pid, leg in claims.select("player_id", "leg").unique().iter_rows():
        hist = pts.filter((pl.col("player_id") == pid) & (pl.col("week") <= leg)).sort("week")
        g = hist.height
        form.append(
            {
                "player_id": pid,
                "leg": leg,
                "games": g,
                "ppg": float(hist["pts"].mean()) if g else None,
                "last3": float(hist["pts"].tail(3).mean()) if g else None,
                "quality": quality(prior.get(pid), float(hist["pts"].sum()), g),
            }
        )
    return claims.join(pl.DataFrame(form), on=["player_id", "leg"], how="left").with_columns(
        (pl.col("bid") / pl.col("faab_before").clip(lower_bound=1)).alias("share")
    )


# ------------------------------------------------------------------ model
# Two parts, both driven by player quality (see `quality`): (1) does an alive opponent bid at all
# (logistic), (2) if so, what share of its FAAB (log-linear, lognormal residual). Simulating both
# gives the max opponent bid.


def alive_by_leg(con, platform: str, league_id: str, season: int) -> dict[int, set[str]]:
    """Teams still alive at each leg's waiver run (the leg's chop happens before its run)."""
    teams = {
        r[0]
        for r in con.execute(
            "SELECT team_id FROM team WHERE platform = ? AND league_id = ?", [platform, league_id]
        ).fetchall()
    }
    chops = dict(
        con.execute(
            """SELECT team_id, min(week) FROM txn
               WHERE platform = ? AND league_id = ? AND season = ?
                 AND type = 'chopped' AND team_id IS NOT NULL GROUP BY team_id""",
            [platform, league_id, season],
        ).fetchall()
    )
    legs = range(1, 19)
    return {w: {t for t in teams if chops.get(t, 99) > w} for w in legs}


PRIOR_GAMES = 4  # last season's PPG counts as this many games of this season


def quality(prior_ppg: float | None, season_pts: float, games: int) -> float | None:
    """Perceived value: this season's PPG shrunk toward last season's (reputation matters early:
    Ja'Marr Chase went for $401 after a 3-point week 1). None for DEF / no data."""
    if prior_ppg is None:
        return season_pts / games if games else None
    return (prior_ppg * PRIOR_GAMES + season_pts) / (PRIOR_GAMES + games)


def _quality(q) -> np.ndarray:
    """Feature row: intercept, quality (DEF/unknown -> 0 with flag)."""
    known = q is not None and not (isinstance(q, float) and math.isnan(q))
    return np.array([1.0, q if known else 0.0, 0.0 if known else 1.0])


def fit(bt: pl.DataFrame, alive: dict[int, set[str]], legs: list[int] | None = None) -> dict:
    """Fit participation + amount on main-run claims (the run with the most claims per leg)."""
    if legs is not None:
        bt = bt.filter(pl.col("leg").is_in(legs))
    main = bt.group_by("leg", "processed_at").len().sort("len", descending=True)
    main = main.unique("leg", keep="first").select("leg", "processed_at")
    bt = bt.join(main, on=["leg", "processed_at"])
    players = bt.group_by("leg", "player_id").agg(pl.col("quality").first())

    # (1) participation: every (player, alive team) pair at that leg; bid > 0 counts as a bid.
    bidders = {
        (lg, p): set(ts)
        for lg, p, ts in bt.filter(pl.col("bid") > 0)
        .group_by("leg", "player_id")
        .agg(pl.col("team_id"))
        .iter_rows()
    }
    X, y = [], []
    for lg, pid, q in players.iter_rows():
        for t in alive[lg]:
            X.append(_quality(q))
            y.append(1.0 if t in bidders.get((lg, pid), ()) else 0.0)
    w_part = _logistic(np.array(X), np.array(y))

    # (2) amount: log share of FAAB for positive bids.
    pos = bt.filter(pl.col("bid") > 0)
    Xa = np.array([_quality(q) for q in pos["quality"].to_list()])
    ya = np.log(pos["share"].clip(upper_bound=1.0).to_numpy())
    w_amt, *_ = np.linalg.lstsq(Xa, ya, rcond=None)
    resid_sd = float(np.std(ya - Xa @ w_amt, ddof=Xa.shape[1]))
    return {"w_part": w_part, "w_amt": w_amt, "resid_sd": resid_sd}


def _logistic(X: np.ndarray, y: np.ndarray, iters: int = 500, l2: float = 1e-3) -> np.ndarray:
    w = np.zeros(X.shape[1])
    for _ in range(iters):  # Newton steps; small ridge keeps it stable
        p = 1 / (1 + np.exp(-X @ w))
        H = X.T @ (X * (p * (1 - p))[:, None]) + l2 * np.eye(len(w))
        w -= np.linalg.solve(H, X.T @ (p - y) + l2 * w)
    return w


def max_opponent_bids(
    model: dict, q, opp_faab: list[int], n_sims: int = 20000, seed: int = 0
) -> np.ndarray:
    """Simulated highest opponent bid for one player (0 when nobody bids)."""
    rng = np.random.default_rng(seed)
    x = _quality(q)
    p_bid = 1 / (1 + np.exp(-x @ model["w_part"]))
    faab = np.asarray(opp_faab, dtype=float)
    share = np.exp(
        x @ model["w_amt"] + model["resid_sd"] * rng.standard_normal((n_sims, len(faab)))
    )
    amt = np.floor(np.minimum(share, 1.0) * faab)
    bids = np.where(rng.random((n_sims, len(faab))) < p_bid, amt, 0.0)
    return bids.max(axis=1)


def p_win(my_bid: float, max_opp: np.ndarray) -> float:
    """Sleeper ties go to waiver priority; treat ties as a coin flip."""
    return float((max_opp < my_bid).mean() + 0.5 * (max_opp == my_bid).mean())


def current_quality(con, season: int, through_week: int) -> dict[str, float]:
    """`quality` for every player as of now (for pricing this week's pool)."""
    prior = weekly_ppr(con, season - 1).group_by("player_id").agg(pl.col("pts").mean())
    prior = dict(prior.iter_rows())
    now = weekly_ppr(con, season).filter(pl.col("week") <= through_week)
    agg = now.group_by("player_id").agg(pl.col("pts").sum(), pl.len().alias("g"))
    out = {pid: quality(prior.get(pid), pts, g) for pid, pts, g in agg.iter_rows()}
    for pid, ppg in prior.items():
        out.setdefault(pid, quality(ppg, 0.0, 0))
    return out
