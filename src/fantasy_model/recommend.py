"""Bid sheet: for each pickup, the bid that best trades win probability against FAAB.

EV(bid) = P(win claim | bid) * (dP(win league) - LAMBDA * bid). Sleeper charges the bid only when
the claim wins. LAMBDA prices a dollar as the league-win probability it buys later in the season."""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import polars as pl

from fantasy_model import bids, fit, survival, value

PRICE_BUMP = 1.15  # backtest: model underpriced winning bids by ~$69 (mostly mid-tier players)
FUTURE_DISCOUNT = 0.6  # a dollar saved buys a pickup with fewer weeks left to help
CONTESTED_MIN = 25  # market median ($) for a player to count when pricing a dollar
MAX_DEF = 2  # DEF rows kept on the sheet
DEFAULT_LAMBDA = 0.0002  # win prob per $ when nothing is contested (~2% per $100; 2026 previews)


@dataclass
class Context:
    platform: str
    league_id: str
    me: str
    season: int
    week: int  # NFL week of the latest snapshot
    slots: list[str]
    alive: list[str]
    faab: dict[str, int]
    names: dict[str, str]
    chop_pending: bool  # this week's lowest team not yet eliminated


def best_bid(max_opp_sorted: np.ndarray, d_p_win: float, lam: float, faab: int):
    """Bid in [0, faab] maximizing P(get | b) * (d_p_win - lam * b). Ties count half.
    Returns (bid, P(get), EV)."""
    grid = np.arange(0, faab + 1)
    below = np.searchsorted(max_opp_sorted, grid, side="left") / len(max_opp_sorted)
    at_or_below = np.searchsorted(max_opp_sorted, grid, side="right") / len(max_opp_sorted)
    p = below + 0.5 * (at_or_below - below)
    ev = p * (d_p_win - lam * grid)
    i = int(ev.argmax())
    return int(grid[i]), float(p[i]), float(ev[i])


def load_context(con, platform: str, league_id: str, assume_chop: str | None = None) -> Context:
    season, week = con.execute(
        "SELECT season, nfl_week FROM snapshot_run WHERE ok ORDER BY started_at DESC LIMIT 1"
    ).fetchall()[0]
    slots, n_teams = con.execute(
        "SELECT roster_slots, total_rosters FROM league WHERE platform = ? AND league_id = ?",
        [platform, league_id],
    ).fetchall()[0]
    slots = json.loads(slots) if isinstance(slots, str) else slots
    snap = value.latest_snapshot(
        con, "team_state_snapshot", f"platform = '{platform}' AND league_id = '{league_id}'"
    )
    state = con.execute(
        """SELECT team_id, faab_remaining, eliminated FROM team_state_snapshot
           WHERE snapshot_id = ? AND platform = ? AND league_id = ?""",
        [snap, platform, league_id],
    ).fetchall()
    alive = sorted(t for t, _, dead in state if not dead and t != assume_chop)
    me = con.execute(
        "SELECT team_id FROM team WHERE platform = ? AND league_id = ? AND is_me",
        [platform, league_id],
    ).fetchall()
    if not me:
        raise SystemExit("no team marked is_me; set [me] sleeper_user_id in config/leagues.toml")
    names = {
        t: (tn or on or t)
        for t, tn, on in con.execute(
            "SELECT team_id, team_name, owner_name FROM team WHERE platform = ? AND league_id = ?",
            [platform, league_id],
        ).fetchall()
    }
    chops_done = n_teams - len(alive)
    return Context(
        platform=platform,
        league_id=league_id,
        me=me[0][0],
        season=int(season),
        week=int(week),
        slots=slots,
        alive=alive,
        faab={t: f for t, f, _ in state},
        names=names,
        chop_pending=chops_done < week,
    )


def recommend(con, ctx: Context, assume_chop: str | None = None, candidates: int = 300) -> dict:
    """Returns {'summary': {...}, 'sheet': DataFrame} for the next waiver run."""
    first = ctx.week + 1  # pickups from this run play from next week on
    sim_weeks = (
        [ctx.week, *range(first, first + 4)] if ctx.chop_pending else list(range(first, first + 4))
    )
    gain_weeks = list(range(first, first + 4))

    vals = fit.adjust_for_injury(
        value.player_values(con, ctx.platform, ctx.league_id, sim_weeks), sim_weeks
    )
    if assume_chop:  # preview: that roster is released to waivers
        vals = vals.with_columns(
            pl.when(pl.col("owner_team_id") == assume_chop)
            .then(None)
            .otherwise(pl.col("owner_team_id"))
            .alias("owner_team_id")
        )

    # Survival model over the remaining chops.
    proj = np.array(
        [
            [fit.lineup_points(vals, t, ctx.slots, sim_weeks)[w] for w in sim_weeks]
            for t in ctx.alive
        ]
    )
    mu = survival.extend_horizon(proj, len(ctx.alive) - 1)
    sigma = survival.score_sigma(con, ctx.platform, ctx.league_id, ctx.week - 1)
    noise = survival.common_noise(len(ctx.alive), mu.shape[1])
    base = survival.simulate(mu, sigma, noise=noise)
    me_i = ctx.alive.index(ctx.me)
    start = 1 if ctx.chop_pending else 0

    # Lineup gain per candidate, then league-win gain.
    gains = fit.roster_gain(vals, ctx.me, ctx.slots, gain_weeks, candidates=candidates)
    gains = gains.filter(pl.col("gain") > 0)
    d_win = [
        survival.pickup_delta(
            mu, me_i, np.array([r[f"gain_w{w}"] for w in gain_weeks]), sigma, noise, start
        )["d_p_win"]
        for r in gains.iter_rows(named=True)
    ]
    gains = gains.with_columns(pl.Series("d_p_win", d_win))

    # Opponent max-bid distributions.
    model = bids.fit(
        bids.bid_table(con, ctx.platform, ctx.league_id, ctx.season),
        bids.alive_by_leg(con, ctx.platform, ctx.league_id, ctx.season),
    )
    qual = bids.current_quality(con, ctx.season, ctx.week)
    opp = [ctx.faab[t] for t in ctx.alive if t != ctx.me]
    my_faab = ctx.faab[ctx.me]
    dist = {
        pid: PRICE_BUMP * bids.max_opponent_bids(model, qual.get(pid), opp)
        for pid in gains["player_id"]
    }

    # Price of a dollar: win-per-dollar on genuinely contested players (near-free DEFs would make
    # money look priceless), discounted because later pickups have fewer weeks to help.
    rates = sorted(
        (
            dw / float(np.median(dist[pid]))
            for pid, dw in gains.select("player_id", "d_p_win").iter_rows()
            if dw > 0 and np.median(dist[pid]) >= CONTESTED_MIN
        ),
        reverse=True,
    )
    lam = FUTURE_DISCOUNT * float(np.median(rates[:10])) if rates else DEFAULT_LAMBDA

    rows = []
    for r in gains.iter_rows(named=True):
        mx = np.sort(dist[r["player_id"]])
        b, p_get, ev = best_bid(mx, r["d_p_win"], lam, my_faab)
        rows.append(
            {
                "player": r["full_name"],
                "pos": r["position"],
                "team": r["nfl_team"],
                "injury": r["injury_status"],
                "pts_gain": r["gain"],
                "d_p_win": r["d_p_win"],
                "bid": b,
                "p_win_claim": p_get,
                "ev": ev,
                "market_median": float(np.median(mx)),
                "drop": r["drop_name"],
                "player_id": r["player_id"],
            }
        )
    sheet = pl.DataFrame(rows).filter(pl.col("bid") > 0).sort("ev", descending=True)
    # One DEF starts; beyond the top few they're interchangeable noise on the sheet.
    sheet = sheet.filter(
        (pl.col("pos") != "DEF") | (pl.col("pos").cum_count().over("pos") <= MAX_DEF)
    )
    return {
        "summary": {
            "team": ctx.names[ctx.me],
            "faab": my_faab,
            "week": ctx.week,
            "chop_pending": ctx.chop_pending,
            "assume_chop": ctx.names.get(assume_chop) if assume_chop else None,
            "p_win": float(base["p_win"][me_i]),
            "p_chop_next": float(base["p_chop"][me_i, 0]),
            "lambda_per_100": lam * 100,
            "sigma": sigma,
        },
        "sheet": sheet,
    }
