"""Roster dynamics: who gets stronger when a team is chopped and its roster hits waivers.

For each possible chopped team c we play out an expected auction of its best players: each goes
to the rival with the highest expected bid (bid model: P(bids) x expected share x FAAB) among
those whose lineup it improves; that team's FAAB drops by its bid. The recipient's lineup gain
(per week, from fit.claim_gain) becomes D[c, t] in survival.simulate.

Each dump also records what each recipient spends (S[c, t]); survival.simulate tracks FAAB per
simulated season, so a team that spent its budget stops collecting (without that, a rich team
wins every future dump: our first run had the richest team at 76% to win the league).

Approximations (not yet validated against outcomes): auctions are priced off today's rosters
and FAAB (a broke team's players don't fall to the next bidder), and only each roster's top
DUMP_TOP players by projection change hands.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fantasy_model import bids, fit

DUMP_TOP = 6  # players per chopped roster worth modelling (rest rarely start)
MIN_GAIN = 0.5  # pts/week: below this a team has no real need, so it isn't a bidder
# Backtest (2026 legs 3-7, 33 contested players, bid model fit on earlier legs only): the
# top-expected-bid team was the actual winner 15% of the time (in its top 3: 45%; richest team:
# 3%; uniform: 9%) and its bid was a median 0.88x the actual price, so payments are scaled up.
# (Before the roster-need feature: 12%, 52%, 0.69x.)
PAY_SCALE = 1.13  # default; the weekly review refits it (review.py -> data/calibration.json)


def pay_scale() -> float:
    from fantasy_model.review import load_calibration

    return load_calibration().get("pay_scale") or PAY_SCALE


def expected_bid(model: dict, q, team: str, faab: int, need: float = 0.0) -> tuple[float, float]:
    """(unconditional mean bid, bid if it bids) from the bid model, capped at the team's FAAB.
    The first ranks likely winners; the second is what a winner actually pays (a team that wins
    pays its real bid, not one averaged with the times it sits out). need: the team's lineup
    gain from the player (pts/week)."""
    x = bids._x(q, 0.0, 9, 0.5, model.get("feats", ()), need)
    p = 1 / (1 + np.exp(-(x @ model["w_part"] + model["team_part"].get(team, 0.0))))
    mean = x @ model["w_amt"] + model["team_amt"].get(team, 0.0)
    share = min(float(np.exp(mean + model["resid_sd"] ** 2 / 2)), 1.0)
    return float(p * share * faab), float(share * faab)


def allocate(
    vals: pl.DataFrame,
    slots: list[str],
    weeks: list[int],
    pool: list[str],
    recipients: list[str],
    faab: dict[str, int],
    model: dict,
    quality: dict,
    gain_cache: dict,
) -> tuple[dict[str, float], dict[str, float]]:
    """Expected auction of `pool` (best first). Returns ({team: weekly gain}, {team: spend}).
    A team's second pickup from the same dump is valued on top of its first (no double count
    for two players at one position)."""
    left = dict(faab)
    scale = pay_scale()
    got: dict[str, float] = {}
    spent: dict[str, float] = {}
    adds: dict[str, list[str]] = {}
    for pid in pool:
        best = None
        for t in recipients:
            key = (pid, t, tuple(adds.get(t, ())))
            if key not in gain_cache:
                g, _ = fit.claim_gain(vals, t, slots, weeks, pid, adds=adds.get(t, []))
                gain_cache[key] = float(np.mean(g))
            if gain_cache[key] < MIN_GAIN or left.get(t, 0) <= 0:
                continue
            # Projected lineup gain stands in for need here (rosters shift as dumps play out).
            rank, pay = expected_bid(model, quality.get(pid), t, left[t], gain_cache[key])
            pay = min(pay * scale, left[t])
            if rank > 0 and (best is None or rank > best[1]):
                best = (t, rank, pay, gain_cache[key])
        if best:
            t, _, pay, gain = best
            got[t] = got.get(t, 0.0) + gain
            spent[t] = spent.get(t, 0.0) + int(pay)
            left[t] -= int(pay)
            adds.setdefault(t, []).append(pid)
    return got, spent


def dump_matrices(vals, ctx, weeks: list[int], model: dict, quality: dict) -> dict:
    """{"first", "rest", "spend_first", "spend_rest", "faab", "now"}: D[c, t] = team t's weekly
    gain if team c is chopped, S[c, t] its spend. "first" applies to the next chop and excludes
    us as a recipient when that chop is pending (our claims from that run are what `recommend`
    optimizes); "now" = rivals' gains from today's pool when the chop already happened."""
    alive = ctx.alive
    n = len(alive)
    cache: dict = {}
    top = {
        c: vals.filter(pl.col("owner_team_id") == c)
        .sort("ros_pts", descending=True)
        .head(DUMP_TOP)["player_id"]
        .to_list()
        for c in alive
    }

    def matrix(exclude: set[str]) -> tuple[np.ndarray, np.ndarray]:
        d, sp = np.zeros((n, n)), np.zeros((n, n))
        for i, c in enumerate(alive):
            rec = [t for t in alive if t != c and t not in exclude]
            got, spent = allocate(
                vals, ctx.slots, weeks, top[c], rec, ctx.faab, model, quality, cache
            )
            for j, t in enumerate(alive):
                d[i, j], sp[i, j] = got.get(t, 0.0), spent.get(t, 0.0)
        return d, sp

    d_rest, s_rest = matrix(set())
    d_first, s_first = matrix({ctx.me}) if ctx.chop_pending else (d_rest, s_rest)
    now = np.zeros(n)
    if not ctx.chop_pending:
        pool = (
            vals.filter(pl.col("owner_team_id").is_null())
            .sort("ros_pts", descending=True)
            .head(DUMP_TOP)["player_id"]
            .to_list()
        )
        rec = [t for t in alive if t != ctx.me]
        got, _ = allocate(vals, ctx.slots, weeks, pool, rec, ctx.faab, model, quality, cache)
        now = np.array([got.get(t, 0.0) for t in alive])
    return {
        "first": d_first,
        "rest": d_rest,
        "spend_first": s_first,
        "spend_rest": s_rest,
        "faab": np.array([float(ctx.faab[t]) for t in alive]),
        "now": now,
    }
