"""What your FAAB is worth: P(win the league) as a function of your balance.

Plays out the rest of the season with auctions of each chopped roster. Rivals bid from the bid
model as a share of what they have left in that simulated season, so prices fall as they spend,
and a team that's out of money stops collecting. You bid in the same auctions by a simple rule,
$k per point of lineup gain per remaining week, either flat or scaled to what you have left; a
balance's value is its best rule. So the curve
includes your rank among rivals, money running out, fewer weeks left, and getting chopped.

Checked against the finished 2025 league: late prices collapse (players rated 20+ went for $10-34
in weeks 11-13), so LATE_PRICE scales rival bids once few teams are left.

Approximations: rosters and each pickup's gain are fixed at today's (FADE discounts a team's later
pickups, since it starts fewer of them), and rivals don't change how they bid.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from fantasy_model import bids as bid_model
from fantasy_model import dynamics, fit, survival

# A team's nth pickup, as a share of its first's gain. Measured with pickup_fade on 2026 rosters.
FADE = np.cumprod([1.0, 0.63, 0.9, 0.89, *[0.9] * 40])
# 2025 weeks 11-13 (7 to 5 teams left): the bid model forecast $100/59/44 for each run's top bid,
# actual $34/15/12. One season and 12 auctions, so it's a rough correction.
LATE_ALIVE, LATE_PRICE = 7, 0.3
RATES = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0)  # $ per point of weekly gain per remaining week
BALANCE_STEP = 50
MONEY_SIMS = 20000  # at 6,000 the 2026 week 9 plan changed with the seed ($191-249, its structure)


@dataclass
class Auctions:
    """Indexed [pool, player, team]; pool len(alive) is today's pool, the rest are chopped rosters.
    p, log_share: the bid model for that team on that player. gain: its weekly lineup gain."""

    p: np.ndarray
    log_share: np.ndarray
    gain: np.ndarray
    resid_sd: float
    level: float


def build_auctions(
    vals: pl.DataFrame, ctx, weeks: list[int], model: dict, quality: dict
) -> Auctions:
    alive = ctx.alive
    size = (len(alive) + 1, dynamics.DUMP_TOP, len(alive))
    p, log_share, gain = np.zeros(size), np.zeros(size), np.zeros(size)
    best = vals.sort("ros_pts", descending=True)
    today = best.filter(pl.col("owner_team_id").is_null())
    pools = [best.filter(pl.col("owner_team_id") == c) for c in alive] + [today]
    cols = [f"pts_w{w}" for w in weeks]
    pts = {r[0]: r[1:] for r in best.select("player_id", *cols).iter_rows()}
    for c, pool in enumerate(pools):
        for i, pid in enumerate(pool.head(dynamics.DUMP_TOP)["player_id"]):
            for j, t in enumerate(alive):
                if c < len(alive) and j == c:
                    continue  # a chopped team doesn't bid on its own roster
                g = float(
                    fit.season_gains(
                        fit.claim_gain(vals, t, ctx.slots, weeks, pid)[0], pts[pid], len(weeks) + 1
                    )[-1]
                )
                gain[c, i, j] = g
                if t == ctx.me or g < dynamics.MIN_GAIN:
                    continue
                x = bid_model._x(quality.get(pid), 0.0, 9, 0.5, model.get("feats", ()), g)
                p[c, i, j] = 1 / (
                    1 + np.exp(-(x @ model["w_part"] + model["team_part"].get(t, 0.0)))
                )
                log_share[c, i, j] = x @ model["w_amt"] + model["team_amt"].get(t, 0.0)
    return Auctions(p, log_share, gain, model["resid_sd"], model.get("level", 1.0))


def _draws(a: Auctions, n_sims: int, n_weeks: int, seed: int = 11) -> tuple[np.ndarray, np.ndarray]:
    """Fixed random numbers per (sim, round, player, team), so balances compare with low noise."""
    rng = np.random.default_rng(seed)
    shape = (n_sims, n_weeks + 1, a.p.shape[1], a.p.shape[2])
    return (
        rng.standard_normal(shape, dtype=np.float32),
        rng.random(shape, dtype=np.float32),
    )


def play_season(
    mu: np.ndarray, sigma: float, noise: np.ndarray, a: Auctions, faab: np.ndarray, me: int,
    rate: float, start: int, draws: tuple, fade: np.ndarray = FADE, scaled: bool = False,
    late_price: float = LATE_PRICE,
) -> dict:  # fmt: skip
    """One balance and bidding rate over every simulated season. mu: (teams, weeks) as in
    survival.simulate. Today's pool sells first, to rivals only: our claims in it are what the
    bid sheet decides. Returns our p_win, rivals' spend per round, our auction wins per season."""
    z, u = draws
    n_sims, n_teams, n_weeks = noise.shape
    scores = mu[None, :, :] + sigma * noise
    if n_weeks > 1:  # the same drift paths as survival.simulate
        steps = np.random.default_rng(7919).standard_normal(noise.shape)
        var = np.array([0.0, survival.DRIFT_V1] + [survival.DRIFT_STEP] * (n_weeks - 2))[:n_weeks]
        scores = scores + np.cumsum(steps * np.sqrt(var)[None, None, :], axis=2)
    alive = np.ones((n_sims, n_teams), dtype=bool)
    money = np.tile(faab.astype(float), (n_sims, 1))
    picks = np.zeros((n_sims, n_teams))
    spend = np.zeros(n_weeks + 1)
    my_wins = np.zeros(n_sims)

    def sell(pool: int, rows: np.ndarray, round_: int, first_week: int, bid_me: bool) -> None:
        for i in range(a.p.shape[1]):
            zz, uu = z[rows, round_, i], u[rows, round_, i]
            share = np.exp(a.log_share[pool, i] + a.resid_sd * zz) * a.level
            late = alive[rows].sum(axis=1, keepdims=True) <= LATE_ALIVE
            share = np.where(late, share * late_price, share)
            amt = np.floor(np.minimum(share, 1.0) * money[rows])
            bids = np.where((uu < a.p[pool, i]) & alive[rows], amt, 0.0)
            bids[:, me] = 0.0
            if bid_me:
                weeks_left = n_weeks - first_week
                want = rate * a.gain[pool, i, me] * weeks_left
                if scaled:  # spend in proportion to the balance left (rate is per $500)
                    want = want * money[rows, me] / 500
                if a.gain[pool, i, me] >= dynamics.MIN_GAIN:
                    bids[:, me] = np.where(
                        alive[rows, me], np.floor(np.minimum(want, money[rows, me])), 0.0
                    )
            # A tie goes to a rival.
            others = np.where(np.arange(n_teams) == me, -1.0, bids)
            rival = others.max(axis=1)
            mine = bids[:, me] > rival
            winner = np.where(mine, me, others.argmax(axis=1))
            paid = np.where(mine, bids[:, me], rival)
            sold = paid > 0
            r, w = rows[sold], winner[sold]
            money[r, w] -= paid[sold]
            spend[round_] += paid[sold & ~mine].sum()
            my_wins[rows[sold & mine]] += 1
            if first_week < n_weeks:
                boost = (
                    a.gain[pool, i, w] * fade[np.minimum(picks[r, w], len(fade) - 1).astype(int)]
                )
                scores[r, w, first_week:] += boost[:, None]
            picks[r, w] += 1

    every = np.arange(n_sims)
    sell(len(faab), every, 0, start, False)
    for w in range(n_weeks):
        still = alive.sum(axis=1) > 1
        loser = np.where(alive, scores[:, :, w], np.inf).argmin(axis=1)
        alive[every[still], loser[still]] = False
        if w + 1 < n_weeks:
            for c in np.unique(loser[still]):
                sell(int(c), every[still & (loser == c)], w + 1, w + 1, True)
    won = alive & (alive.sum(axis=1, keepdims=True) == 1)
    return {
        "p_win": float(won[:, me].mean()),
        "p_win_all": won.mean(axis=0),
        "spend": spend / n_sims,
        "my_wins": float(my_wins.mean()),
        "money_end": money.mean(axis=0),
    }


def value_curve(
    mu: np.ndarray, sigma: float, noise: np.ndarray, a: Auctions, faab: np.ndarray, me: int,
    start: int, fade: np.ndarray = FADE, draws: tuple | None = None, late_price: float = LATE_PRICE,
) -> dict:  # fmt: skip
    """{balances, values, rules}: P(win) at each balance with its best bidding rule, smoothed by
    concave_up_to. `array` holds the value at every whole dollar up to our balance."""
    n_sims, _, n_weeks = noise.shape
    draws = draws or _draws(a, n_sims, n_weeks)
    top = int(faab[me])
    grid = sorted({*range(0, top, BALANCE_STEP), top})
    values, rates = [], []
    for f in grid:
        bal = faab.copy()
        bal[me] = f
        rules = [(r, sc) for r in RATES for sc in (False, True)] if f > 0 else [(0.0, False)]
        runs = [
            play_season(mu, sigma, noise, a, bal, me, r, start, draws, fade, sc, late_price)[
                "p_win"
            ]
            for r, sc in rules
        ]
        values.append(max(runs))
        rates.append(rules[int(np.argmax(runs))])
    values = concave_up_to(np.array(grid, dtype=float), np.array(values))
    return {
        "balances": np.array(grid),
        "values": values,
        "rules": rates,
        "array": np.interp(np.arange(top + 1), grid, values),
    }


def concave_up_to(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Smallest concave, non-decreasing curve on or above y. Simulation noise otherwise creates
    steps, and a best bid that jumps to whichever step is nearest."""
    y = np.maximum.accumulate(y)
    hull = [0]
    for i in range(1, len(x)):
        while len(hull) > 1:
            a, b = hull[-2], hull[-1]
            if (y[b] - y[a]) * (x[i] - x[a]) <= (y[i] - y[a]) * (x[b] - x[a]):
                hull.pop()
            else:
                break
        hull.append(i)
    return np.interp(x, x[hull], y[hull])


def league_curve(st, ctx, n_sims: int = MONEY_SIMS) -> np.ndarray:
    """Our value curve for the coming run, from recommend.league_state's output."""
    mu = survival.extend_horizon(st.proj, len(ctx.alive) - 1)
    if mu.shape[1] == 0:
        return np.zeros(int(ctx.faab[ctx.me]) + 1)
    a = build_auctions(st.vals, ctx, st.gain_weeks, st.model, st.quality)
    faab = np.array([float(ctx.faab[t]) for t in ctx.alive])
    return value_curve(mu, st.sigma, st.noise[:n_sims], a, faab, st.me_i, st.start)["array"]


def money_cost(curve: np.ndarray, balance: int, limit: int) -> np.ndarray:
    """Win probability lost by spending b = 0..limit from `balance`."""
    bids = np.arange(limit + 1)
    return curve[balance] - curve[np.maximum(balance - bids, 0)]


def walk_away(curve: np.ndarray, balance: int, value: float) -> int:
    """The most a claim worth `value` (win probability) is worth paying from `balance`."""
    cost = money_cost(curve, balance, balance)
    ok = np.nonzero(cost <= value)[0]
    return int(ok.max()) if len(ok) else 0


def pickup_fade(vals: pl.DataFrame, slots: list[str], weeks: list[int], teams: list[str],
                pool: int = 40, picks: int = 4) -> list[float]:  # fmt: skip
    """Each next pickup's lineup gain as a share of the previous one, across teams: every team
    greedily takes the best of the top `pool` players it doesn't own, and we compare gains."""
    best = vals.sort("ros_pts", descending=True)["player_id"].head(pool * 3).to_list()
    owners = dict(vals.select("player_id", "owner_team_id").iter_rows())
    out: list[list[float]] = [[] for _ in range(picks - 1)]
    for t in teams:
        others = [p for p in best if owners.get(p) != t][:pool]
        adds: list[str] = []
        gains: list[float] = []
        for _ in range(picks):
            g = {p: float(np.mean(fit.claim_gain(vals, t, slots, weeks, p, adds)[0]))
                 for p in others if p not in adds}  # fmt: skip
            pick = max(g, key=g.get)
            gains.append(g[pick])
            adds.append(pick)
        for k in range(picks - 1):
            if gains[k] > 0.5:
                out[k].append(gains[k + 1] / gains[k])
    return [float(np.mean(x)) if x else 0.0 for x in out]
