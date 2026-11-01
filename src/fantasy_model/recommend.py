"""Bid sheet: for each pickup, the bid that best trades win probability against FAAB.

EV(bid) = P(win claim | bid) * (dP(win league) - LAMBDA * bid). Sleeper charges the bid only when
the claim wins. LAMBDA prices a dollar as the league-win probability it buys later in the season."""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import polars as pl

from fantasy_model import bids, dynamics, fit, survival, value

# Was 1.15 to offset underpricing; with per-team effects the backtest median bias is -$24 and any
# scale-up worsens CRPS (1.0: 96.7, 1.15: 102.7), so no bump.
PRICE_BUMP = 1.0
CONTESTED_MIN = 25  # market median ($) for a player to count when pricing a dollar
MAX_DEF = 2  # DEF rows kept on the sheet
MAX_CLAIMS, PLAN_POOL = 4, 8  # joint plan: up to 4 claims chosen from the top 8 sheet rows
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


def future_discount(p_chop: np.ndarray) -> float:
    """What a saved dollar is worth relative to spending it now: the average, over the future
    waiver runs, of P(we're still alive then) x (share of the remaining chops a pickup made
    then still plays). A team likely to be chopped soon should spend now; a safe team saves.
    p_chop: our P(eliminated) per remaining chop, starting with the next one."""
    n = len(p_chop)
    if n < 2:
        return 0.0
    alive = 1 - np.cumsum(p_chop)
    return float(np.mean([alive[k - 1] * (n - k) / n for k in range(1, n)]))


def best_bid(max_opp_sorted: np.ndarray, d_p_win: float, lam: float, faab: int, deny_sorted=None):
    """Bid in [0, faab] maximizing P(get | b) * (d_p_win - lam * b) + denial(b). Ties count half.
    deny_sorted: per-sim loss to our P(win) if that sim's top rival bidder gets the player,
    aligned with max_opp_sorted; denial(b) = expected loss avoided by outbidding him.
    Returns (bid, P(get), EV)."""
    n = len(max_opp_sorted)
    grid = np.arange(0, faab + 1)
    lt = np.searchsorted(max_opp_sorted, grid, side="left")
    le = np.searchsorted(max_opp_sorted, grid, side="right")
    p = (lt + 0.5 * (le - lt)) / n
    ev = p * (d_p_win - lam * grid)
    if deny_sorted is not None:
        cum = np.concatenate([[0.0], np.cumsum(deny_sorted)])
        ev = ev + (cum[lt] + 0.5 * (cum[le] - cum[lt])) / n
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


@dataclass
class LeagueState:
    vals: pl.DataFrame  # injury-adjusted projections per player, pts_w<N> for sim_weeks
    sim_weeks: list[int]
    gain_weeks: list[int]
    proj: np.ndarray  # (alive teams, sim_weeks) best projected lineup points
    mu: np.ndarray  # proj extended to every remaining chop
    sigma: float
    noise: np.ndarray
    base: dict  # survival.simulate output
    model: dict  # bid model
    quality: dict  # bid-model quality per player
    dumps: dict  # rivals' gains/spend from future chops (dynamics.dump_matrices)
    me_i: int
    start: int  # first mu column a pickup from this run affects


def league_state(con, ctx: Context, assume_chop: str | None = None) -> LeagueState:
    """Projections, lineups and the survival simulation for the next waiver run."""
    first = ctx.week + 1  # pickups from this run play from next week on
    sim_weeks = (
        [ctx.week, *range(first, first + 4)] if ctx.chop_pending else list(range(first, first + 4))
    )
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
    proj = np.array(
        [
            [fit.lineup_points(vals, t, ctx.slots, sim_weeks)[w] for w in sim_weeks]
            for t in ctx.alive
        ]
    )
    gain_weeks = list(range(first, first + 4))
    model = bids.fit(
        bids.bid_table(con, ctx.platform, ctx.league_id, ctx.season),
        bids.alive_by_leg(con, ctx.platform, ctx.league_id, ctx.season),
    )
    quality = bids.current_quality(con, ctx.season, ctx.week)
    dumps = dynamics.dump_matrices(vals, ctx, gain_weeks, model, quality)
    mu = survival.extend_horizon(proj, len(ctx.alive) - 1) + dumps["now"][:, None]
    sigma = survival.score_sigma(con, ctx.platform, ctx.league_id, ctx.week - 1)
    noise = survival.common_noise(len(ctx.alive), mu.shape[1])
    return LeagueState(
        vals=vals,
        sim_weeks=sim_weeks,
        gain_weeks=gain_weeks,
        proj=proj,
        mu=mu,
        sigma=sigma,
        noise=noise,
        base=survival.simulate(mu, sigma, noise=noise, dumps=dumps),
        me_i=ctx.alive.index(ctx.me),
        start=1 if ctx.chop_pending else 0,
        model=model,
        quality=quality,
        dumps=dumps,
    )


def recommend(con, ctx: Context, assume_chop: str | None = None, candidates: int = 300) -> dict:
    """Returns {'summary', 'sheet', 'plan'} for the next waiver run."""
    st = league_state(con, ctx, assume_chop)
    vals, gain_weeks, mu, sigma = st.vals, st.gain_weeks, st.mu, st.sigma
    noise, base, me_i, start, dumps = st.noise, st.base, st.me_i, st.start, st.dumps

    # Lineup gain per candidate, then league-win gain.
    gains = fit.roster_gain(vals, ctx.me, ctx.slots, gain_weeks, candidates=candidates)
    gains = gains.filter(pl.col("gain") > 0)
    d_win = [
        survival.pickup_delta(
            mu, me_i, np.array([r[f"gain_w{w}"] for w in gain_weeks]), sigma, noise, start, dumps
        )["d_p_win"]
        for r in gains.iter_rows(named=True)
    ]
    gains = gains.with_columns(pl.Series("d_p_win", d_win))

    # Opponent max-bid distributions.
    model, qual = st.model, st.quality
    opp = [(t, ctx.faab[t], 0.5) for t in ctx.alive if t != ctx.me]
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
    discount = future_discount(base["p_chop"][me_i, start:])
    lam = discount * float(np.median(rates[:10])) if rates else DEFAULT_LAMBDA

    # Denial: for the most valuable candidates, who would win him if we don't, and what that
    # rival's gain costs our P(win). Ordered by standalone value so the costly sims stay few.
    deny = {}
    order = gains.sort("d_p_win", descending=True)["player_id"].head(PLAN_POOL).to_list()
    for pid in order:
        mx, who = bids.max_opponent_bids(model, qual.get(pid), opp, return_winner=True)
        loss = np.zeros(len(opp))
        for k, (t, _, _) in enumerate(opp):
            if (who == k).mean() < 0.01:
                continue
            g_t, _ = fit.claim_gain(vals, t, ctx.slots, gain_weeks, pid)
            if sum(g_t) <= 0:
                continue
            d = survival.pickup_delta(
                mu, ctx.alive.index(t), np.array(g_t), sigma, noise, start, dumps
            )
            loss[k] = max(-d["d_p_win_all"][me_i], 0.0)
        per_sim = np.where(who >= 0, loss[np.maximum(who, 0)], 0.0)
        o = np.argsort(PRICE_BUMP * mx, kind="stable")
        dist[pid] = PRICE_BUMP * mx
        deny[pid] = (o, per_sim[o])

    rows = []
    for r in gains.iter_rows(named=True):
        mx = np.sort(dist[r["player_id"]])
        dn = deny.get(r["player_id"])
        b, p_get, ev = best_bid(mx, r["d_p_win"], lam, my_faab, dn[1] if dn else None)
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
                "deny": float(dn[1].mean()) if dn else 0.0,
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
    plan = claim_plan(
        sheet,
        vals,
        ctx,
        gain_weeks,
        mu,
        me_i,
        sigma,
        noise,
        start,
        dist,
        lam,
        my_faab,
        deny,
        st_dumps=dumps,
    )
    return {
        "plan": plan,
        "summary": {
            "team": ctx.names[ctx.me],
            "faab": my_faab,
            "week": ctx.week,
            "chop_pending": ctx.chop_pending,
            "assume_chop": ctx.names.get(assume_chop) if assume_chop else None,
            "p_win": float(base["p_win"][me_i]),
            "p_chop_next": float(base["p_chop"][me_i, 0]),
            "lambda_per_100": lam * 100,
            "faab_discount": discount,
            "sigma": sigma,
        },
        "sheet": sheet,
    }


def claim_plan(
    sheet, vals, ctx, gain_weeks, mu, me_i, sigma, noise, start, dist, lam, faab, deny=None,
    max_claims: int = MAX_CLAIMS, pool: int = PLAN_POOL, st_dumps=None,
) -> pl.DataFrame:  # fmt: skip
    """Greedy joint plan. Each step re-values every remaining candidate given the claims already
    chosen: averaged over which of them win (their P(get)), using the roster those wins produce,
    with a drop no earlier claim uses, and a bid that keeps the plan's total within FAAB."""
    from itertools import product

    cands = sheet.head(pool).select("player_id", "player", "pos").rows()
    chosen: list[dict] = []
    for _ in range(max_claims):
        budget = faab - sum(c["bid"] for c in chosen)
        best = None
        for pid, name, pos in cands:
            if budget <= 0 or any(c["player_id"] == pid for c in chosen):
                continue
            d_win = 0.0
            for outcome in product((True, False), repeat=len(chosen)):
                pairs = list(zip(chosen, outcome, strict=True))
                prob = float(np.prod([c["p_get"] if w else 1 - c["p_get"] for c, w in pairs]))
                won = [c for c, w in pairs if w]
                adds = [c["player_id"] for c in won]
                drops = [c["drop_id"] for c in won]
                used = {c["drop_id"] for c in chosen}
                gains, _ = fit.claim_gain(
                    vals, ctx.me, ctx.slots, gain_weeks, pid, adds, drops, used
                )
                # Base team strength in this outcome includes the earlier wins' gains.
                bumped = mu.copy()
                for c in won:
                    g = np.array(c["gains"])
                    span = mu.shape[1] - start
                    g = np.concatenate([g, np.full(max(span - len(g), 0), g.mean())])[:span]
                    bumped[me_i, start:] += g
                d = survival.pickup_delta(
                    bumped, me_i, np.array(gains), sigma, noise, start, st_dumps
                )
                d_win += prob * d["d_p_win"]
            dn = (deny or {}).get(pid)
            b, p_get, ev = best_bid(np.sort(dist[pid]), d_win, lam, budget, dn[1] if dn else None)
            if b > 0 and ev > 0 and (best is None or ev > best["ev"]):
                g0, drop0 = fit.claim_gain(
                    vals, ctx.me, ctx.slots, gain_weeks, pid,
                    no_drop={c["drop_id"] for c in chosen},
                )  # fmt: skip
                best = {"player_id": pid, "player": name, "pos": pos, "bid": b, "p_get": p_get,
                        "ev": ev, "d_p_win": d_win, "drop_id": drop0, "gains": g0}  # fmt: skip
        if best is None:
            break
        chosen.append(best)
    names = dict(vals.select("player_id", "full_name").iter_rows())
    return pl.DataFrame(
        [
            {
                "order": i + 1,
                "player": c["player"],
                "pos": c["pos"],
                "bid": c["bid"],
                "p_get": c["p_get"],
                "d_p_win": c["d_p_win"],
                "drop": names.get(c["drop_id"]),
                "player_id": c["player_id"],
            }  # fmt: skip
            for i, c in enumerate(chosen)
        ],
        schema={
            "order": pl.Int64,
            "player": pl.Utf8,
            "pos": pl.Utf8,
            "bid": pl.Int64,
            "p_get": pl.Float64,
            "d_p_win": pl.Float64,
            "drop": pl.Utf8,
            "player_id": pl.Utf8,
        },  # fmt: skip
    )
