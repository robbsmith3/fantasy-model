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


# Backtest (`price_backtest`, 2026 legs 3-7, 33 contested auctions, CRPS lower = better):
# base 103.3; +competition 105.1; +desperation 102.8; +leg 169.9 (extrapolates); +team effects
# 96.7. So only shrunken team effects are on; comp/desp stay available for re-testing.
DEFAULT_FEATURES: tuple[str, ...] = ()
TEAM_SHRINK = {"part": 60.0, "amt": 8.0}  # pseudo-observations pulling team effects to 0


def _x(q, comp: float, leg: int, desp: float, feats=DEFAULT_FEATURES) -> np.ndarray:
    """Feature row for one (opponent, player): intercept, quality (DEF/unknown -> 0 + flag),
    competition = log(1 + better players in the same pool), leg/18, opponent desperation."""
    known = q is not None and not (isinstance(q, float) and math.isnan(q))
    row = [1.0, q if known else 0.0, 0.0 if known else 1.0]
    if "comp" in feats:
        row.append(comp)
    if "leg" in feats:
        row.append(leg / 18)
    if "desp" in feats:
        row.append(desp)
    return np.array(row)


def competition(qs: list) -> list[float]:
    """log(1 + number of players in the pool with higher quality), per player."""
    vals = np.array([-1.0 if v is None else v for v in qs])
    return [float(np.log1p((vals > v).sum())) for v in vals]


def desperation(con, platform: str, league_id: str, season: int, leg: int, alive) -> dict:
    """Percentile of each alive team's mean score over its last 3 weeks (<= leg), among alive
    teams: 0 = scoring best, 1 = scoring worst (closest to the chop)."""
    rows = con.execute(
        """SELECT team_id, avg(points) FROM (
               SELECT team_id, points, row_number() OVER (PARTITION BY team_id ORDER BY week DESC) r
               FROM team_week WHERE platform = ? AND league_id = ? AND season = ? AND week <= ?
                 AND points > 0) WHERE r <= 3 GROUP BY team_id""",
        [platform, league_id, season, leg],
    ).fetchall()
    avg = {t: a for t, a in rows if t in alive}
    order = sorted(avg, key=lambda t: -avg[t])
    n = max(len(order) - 1, 1)
    return {t: i / n for i, t in enumerate(order)} | {t: 0.5 for t in alive if t not in avg}


def main_runs(bt: pl.DataFrame) -> pl.DataFrame:
    """Claims from each leg's main waiver run (the run with the most claims)."""
    main = bt.group_by("leg", "processed_at").len()
    main = main.sort(["leg", "len", "processed_at"], descending=[False, True, False])
    main = main.unique("leg", keep="first", maintain_order=True).select("leg", "processed_at")
    return bt.join(main, on=["leg", "processed_at"]).sort("transaction_id", "player_id")


def fit(
    bt: pl.DataFrame,
    alive: dict[int, set[str]],
    legs: list[int] | None = None,
    desp: dict[int, dict[str, float]] | None = None,
    feats=DEFAULT_FEATURES,
    team_effects: bool = True,
    calibrated: bool = True,
) -> dict:
    """Fit participation + amount on main-run claims. desp: {leg: {team: desperation}}.
    calibrated: widen the residual spread by the weekly review's spread_k (see review.py);
    backtests pass False so they grade the raw model."""
    if legs is not None:
        bt = bt.filter(pl.col("leg").is_in(legs))
    bt = main_runs(bt)
    players = bt.group_by("leg", "player_id").agg(pl.col("quality").first())
    comp = {}
    for (lg,), g in players.group_by("leg"):
        keys = [(lg, p) for p in g["player_id"]]
        comp.update(zip(keys, competition(g["quality"].to_list()), strict=True))
    d = desp or {}

    # (1) participation: every (player, alive team) pair at that leg; bid > 0 counts as a bid.
    bidders = {
        (lg, p): set(ts)
        for lg, p, ts in bt.filter(pl.col("bid") > 0)
        .group_by("leg", "player_id")
        .agg(pl.col("team_id"))
        .iter_rows()
    }
    X, y, tp = [], [], []
    for lg, pid, q in players.iter_rows():
        for t in sorted(alive[lg]):
            X.append(_x(q, comp[(lg, pid)], lg, d.get(lg, {}).get(t, 0.5), feats))
            y.append(1.0 if t in bidders.get((lg, pid), ()) else 0.0)
            tp.append(t)
    X, y = np.array(X), np.array(y)
    w_part = _logistic(X, y)

    # (2) amount: log share of FAAB for positive bids.
    pos = bt.filter(pl.col("bid") > 0)
    Xa = np.array(
        [
            _x(q, comp[(lg, p)], lg, d.get(lg, {}).get(t, 0.5), feats)
            for q, lg, p, t in pos.select("quality", "leg", "player_id", "team_id").iter_rows()
        ]
    )
    ya = np.log(pos["share"].clip(upper_bound=1.0).to_numpy())
    w_amt, *_ = np.linalg.lstsq(Xa, ya, rcond=None)
    res_a = ya - Xa @ w_amt
    model = {"w_part": w_part, "w_amt": w_amt, "feats": feats, "team_part": {}, "team_amt": {}}
    if team_effects:
        # Shrunken per-team offsets: logit-scale for participation, log-share for amount.
        p = 1 / (1 + np.exp(-X @ w_part))
        for t in sorted(set(tp)):
            m = np.array(tp) == t
            model["team_part"][t] = float((y[m] - p[m]).sum() / (p[m] * (1 - p[m])).sum()
                                          * m.sum() / (m.sum() + TEAM_SHRINK["part"]))  # fmt: skip
        teams_a = pos["team_id"].to_list()
        for t in sorted(set(teams_a)):
            m = np.array(teams_a) == t
            model["team_amt"][t] = float(res_a[m].sum() / (m.sum() + TEAM_SHRINK["amt"]))
        res_a = res_a - np.array([model["team_amt"][t] for t in teams_a])
    model["resid_sd"] = float(np.std(res_a, ddof=Xa.shape[1]))
    if calibrated:
        from fantasy_model.review import load_calibration

        cal = load_calibration()
        model["resid_sd"] *= cal.get("spread_k", 1.0)
        model["level"] = cal.get("level_s", 1.0)
    return model


def _logistic(X: np.ndarray, y: np.ndarray, iters: int = 500, l2: float = 1e-3) -> np.ndarray:
    w = np.zeros(X.shape[1])
    for _ in range(iters):  # Newton steps; small ridge keeps it stable
        p = 1 / (1 + np.exp(-X @ w))
        H = X.T @ (X * (p * (1 - p))[:, None]) + l2 * np.eye(len(w))
        w -= np.linalg.solve(H, X.T @ (p - y) + l2 * w)
    return w


def max_opponent_bids(
    model: dict,
    q,
    opponents: list[tuple[str, int, float]],
    comp: float = 0.0,
    leg: int = 9,
    n_sims: int = 20000,
    seed: int = 0,
    return_winner: bool = False,
):
    """Simulated highest opponent bid for one player (0 when nobody bids).
    opponents: (team_id, faab, desperation) for every alive rival. With return_winner, also the
    index into `opponents` of the top bidder per sim (-1 when nobody bids)."""
    rng = np.random.default_rng(seed)
    feats = model.get("feats", ())
    xs = np.array([_x(q, comp, leg, dsp, feats) for _, _, dsp in opponents])
    team_p = np.array([model["team_part"].get(t, 0.0) for t, _, _ in opponents])
    p_bid = 1 / (1 + np.exp(-(xs @ model["w_part"] + team_p)))
    mean = xs @ model["w_amt"] + np.array([model["team_amt"].get(t, 0.0) for t, _, _ in opponents])
    faab = np.array([f for _, f, _ in opponents], dtype=float)
    share = np.exp(mean[None, :] + model["resid_sd"] * rng.standard_normal((n_sims, len(faab))))
    level = model.get("level", 1.0)  # weekly review's level correction (review.py)
    amt = np.floor(np.minimum(share * level, 1.0) * faab[None, :])
    bids = np.where(rng.random((n_sims, len(faab))) < p_bid[None, :], amt, 0.0)
    top = bids.max(axis=1)
    if return_winner:
        return top, np.where(top > 0, bids.argmax(axis=1), -1)
    return top


def crps(samples: np.ndarray, y: float) -> float:
    """Continuous ranked probability score of a sample forecast (lower is better)."""
    s = np.sort(samples)
    n = len(s)
    return float(np.abs(s - y).mean() - (2 * np.arange(1, n + 1) - n - 1) @ s / n**2)


def price_backtest(
    con, platform: str, league_id: str, season: int, legs: list[int], min_win: int = 20,
    scale: float = 1.0, **fit_kw,
) -> dict:  # fmt: skip
    """Walk-forward: fit on earlier legs, forecast the winning price of every contested
    main-run player (winning bid >= min_win). Returns CRPS, 80% coverage, median bias, width."""
    bt = bid_table(con, platform, league_id, season)
    alive = alive_by_leg(con, platform, league_id, season)
    desp = {
        lg: desperation(con, platform, league_id, season, lg, alive[lg])
        for lg in range(1, max(legs) + 1)
    }
    out = []
    for leg in legs:
        model = fit(bt, alive, legs=list(range(1, leg)), desp=desp, calibrated=False, **fit_kw)
        run = main_runs(bt.filter(pl.col("leg") == leg))
        mp = run["processed_at"][0]
        spent = dict(bt.filter(pl.col("won") & (pl.col("processed_at") < mp))
                     .group_by("team_id").agg(pl.col("bid").sum()).iter_rows())  # fmt: skip
        budget = 1000
        opps = [(t, budget - spent.get(t, 0), desp[leg].get(t, 0.5)) for t in sorted(alive[leg])]
        pool = run.group_by("player_id").agg(
            pl.col("quality").first(), pl.col("bid").max().alias("win")
        )
        pool = pool.with_columns(pl.Series("comp", competition(pool["quality"].to_list())))
        contested = pool.filter(pl.col("win") >= min_win).sort("player_id")
        for pid, q, win, cp in contested.iter_rows():
            mx = scale * max_opponent_bids(model, q, opps, cp, leg)
            lo, med, hi = np.quantile(mx, [0.1, 0.5, 0.9])
            out.append((leg, pid, win, crps(mx, win), lo <= win <= hi, med - win, hi - lo))
    a = np.array([r[2:] for r in out], dtype=float)
    return {"n": len(out), "crps": a[:, 1].mean(), "cover80": a[:, 2].mean(),
            "bias_median": a[:, 3].mean(), "width80": a[:, 4].mean()}  # fmt: skip


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
