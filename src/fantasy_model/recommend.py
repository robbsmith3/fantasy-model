"""Bid sheet: for each pickup, the bid that best trades win probability against FAAB.

EV(bid) = P(win claim | bid) * (dP(win league) - money cost of bid). Sleeper charges the bid only
when the claim wins. The money cost is the league-win probability those dollars would buy later in
the season, simulated at each balance (faab_value), so each extra dollar costs more than the
last."""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import polars as pl

from fantasy_model import bids, dynamics, faab_value, fit, survival, value

# Was 1.15 to offset underpricing; with per-team effects the backtest median bias is -$24 and any
# scale-up worsens CRPS (1.0: 96.7, 1.15: 102.7), so no bump.
PRICE_BUMP = 1.0
MAX_DEF = 2  # DEF rows kept on the sheet
MAX_CLAIMS, PLAN_POOL = 4, 8  # joint plan: up to 4 claims chosen from the top 8 sheet rows
MARKET_TOP, MARKET_POOL = 15, 100  # market table: top 15 of the 100 best available by projection
MARKET_SKIP = ("QB", "DEF")


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


def bid_curve(max_opp_sorted: np.ndarray, faab: int, bid: int, points: int = 40) -> list:
    """[[bid, P(get)], ...] from $0 to a bit past where the field tops out (for the UI)."""
    hi = float(np.quantile(max_opp_sorted, 0.95)) * 1.25 if len(max_opp_sorted) else 0.0
    top = int(min(faab, max(hi, bid * 1.5, 20)))
    grid = np.unique(np.linspace(0, top, points).round().astype(int))
    n = len(max_opp_sorted)
    lt = np.searchsorted(max_opp_sorted, grid, side="left")
    le = np.searchsorted(max_opp_sorted, grid, side="right")
    p = (lt + 0.5 * (le - lt)) / n
    return [[float(g), round(float(x), 4)] for g, x in zip(grid, p, strict=True)]


def best_bid(
    max_opp_sorted: np.ndarray, d_p_win: float, lam: float, faab: int, deny_sorted=None, cost=None
):
    """Bid in [0, faab] maximizing P(get | b) * (d_p_win - lam * b) + denial(b). Ties count half.
    deny_sorted: per-sim loss to our P(win) if that sim's top rival bidder gets the player,
    aligned with max_opp_sorted; denial(b) = expected loss avoided by outbidding him.
    cost: win probability lost by spending b = 0..faab, replacing lam * b (faab_value.money_cost).
    Returns (bid, P(get), EV)."""
    n = len(max_opp_sorted)
    grid = np.arange(0, faab + 1)
    lt = np.searchsorted(max_opp_sorted, grid, side="left")
    le = np.searchsorted(max_opp_sorted, grid, side="right")
    p = (lt + 0.5 * (le - lt)) / n
    ev = p * (d_p_win - (lam * grid if cost is None else cost))
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
        value.player_values(con, ctx.platform, ctx.league_id, sim_weeks),
        sim_weeks,
        fit.status_week(con, ctx.season),
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
    bt = bids.bid_table(con, ctx.platform, ctx.league_id, ctx.season)
    model = bids.fit(
        bt,
        bids.alive_by_leg(con, ctx.platform, ctx.league_id, ctx.season),
        needs=bids.need_table(con, ctx.platform, ctx.league_id, ctx.season, bt),
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


def market_outlook(
    vals: pl.DataFrame, model: dict, quality: dict, opp: list, slots: list[str], names: dict,
    chance: dict | None = None, top: int = MARKET_TOP, pool: int = MARKET_POOL,
) -> pl.DataFrame:  # fmt: skip
    """Available players rivals are most likely to claim: the chance anyone does (`chance`, from
    bids.claim_chances; else the bid model, which overstates it), and the winning bid when one
    does (median and 80% range), from the bid model. Our claims don't move it."""
    cands = (
        vals.filter(
            pl.col("owner_team_id").is_null()
            & ~pl.col("position").is_in(MARKET_SKIP)
            & (pl.col("ros_pts") > 0)
        )
        .sort("ros_pts", descending=True)
        .head(pool)
    )
    teams = [t for t, _, _ in opp]
    need = bids.current_needs(vals, quality, slots, teams, cands["player_id"])
    rows = []
    for r in cands.iter_rows(named=True):
        pid = r["player_id"]
        mx, who = bids.max_opponent_bids(
            model, quality.get(pid), opp, return_winner=True, needs=[need[(t, pid)] for t in teams]
        )
        won = mx[mx > 0]
        if not len(won):
            continue
        shares = np.bincount(who[who >= 0], minlength=len(opp)) / len(won)
        k = int(shares.argmax())
        rows.append(
            {
                "player": r["full_name"],
                "pos": r["position"],
                "team": r["nfl_team"],
                "injury": r["injury_status"],
                "p_claimed": (chance or {}).get(pid, len(won) / len(mx)),
                "bid": float(np.median(won)),
                "bid_lo": float(np.quantile(won, 0.1)),
                "bid_hi": float(np.quantile(won, 0.9)),
                "rival": names.get(teams[k], teams[k]),
                "rival_p": float(shares[k]),
                "player_id": pid,
            }
        )
    rows.sort(key=lambda x: (-x["p_claimed"], -x["bid"]))
    return pl.DataFrame(
        rows[:top],
        schema={
            "player": pl.Utf8, "pos": pl.Utf8, "team": pl.Utf8, "injury": pl.Utf8,
            "p_claimed": pl.Float64, "bid": pl.Float64, "bid_lo": pl.Float64, "bid_hi": pl.Float64,
            "rival": pl.Utf8, "rival_p": pl.Float64, "player_id": pl.Utf8,
        },
    )  # fmt: skip


def recommend(
    con, ctx: Context, assume_chop: str | None = None, candidates: int = 300, curve=None
) -> dict:
    """Returns {'summary', 'sheet', 'plan', 'market'} for the next waiver run."""
    st = league_state(con, ctx, assume_chop)
    vals, gain_weeks, mu, sigma = st.vals, st.gain_weeks, st.mu, st.sigma
    noise, base, me_i, start, dumps = st.noise, st.base, st.me_i, st.start, st.dumps

    pts_cols = [f"pts_w{w}" for w in gain_weeks]
    played = {x[0]: x[1:] for x in vals.select("player_id", *pts_cols).iter_rows()}

    def _season(g, pid):
        return fit.season_gains(g, played[pid], mu.shape[1] - start)

    # Lineup gain per candidate, then league-win gain.
    gains = fit.roster_gain(vals, ctx.me, ctx.slots, gain_weeks, candidates=candidates)
    gains = gains.filter(pl.col("gain") > 0)
    d_win = [
        survival.pickup_delta(
            mu,
            me_i,
            _season([r[f"gain_w{w}"] for w in gain_weeks], r["player_id"]),
            sigma,
            noise,
            start,
            dumps,
        )["d_p_win"]
        for r in gains.iter_rows(named=True)
    ]
    gains = gains.with_columns(pl.Series("d_p_win", d_win))

    # Opponent max-bid distributions.
    model, qual = st.model, st.quality
    opp = [(t, ctx.faab[t], 0.5) for t in ctx.alive if t != ctx.me]
    my_faab = ctx.faab[ctx.me]
    need = bids.current_needs(vals, qual, ctx.slots, [t for t, _, _ in opp], gains["player_id"])

    def opp_needs(pid):
        return [need[(t, pid)] for t, _, _ in opp]

    dist = {
        pid: PRICE_BUMP * bids.max_opponent_bids(model, qual.get(pid), opp, needs=opp_needs(pid))
        for pid in gains["player_id"]
    }

    # What a dollar buys later, at each balance. Replaces a flat price per dollar.
    if curve is None:
        curve = faab_value.league_curve(st, ctx)
    cost_all = faab_value.money_cost(curve, my_faab, my_faab)

    # Denial: for the most valuable candidates, who would win him if we don't, and what that
    # rival's gain costs our P(win). Ordered by standalone value so the costly sims stay few.
    deny, rival = {}, {}
    order = gains.sort("d_p_win", descending=True)["player_id"].head(PLAN_POOL).to_list()
    for pid in order:
        mx, who = bids.max_opponent_bids(
            model, qual.get(pid), opp, return_winner=True, needs=opp_needs(pid)
        )
        loss = np.zeros(len(opp))
        for k, (t, _, _) in enumerate(opp):
            if (who == k).mean() < 0.01:
                continue
            g_t, _ = fit.claim_gain(vals, t, ctx.slots, gain_weeks, pid)
            if sum(g_t) <= 0:
                continue
            d = survival.pickup_delta(
                mu, ctx.alive.index(t), _season(g_t, pid), sigma, noise, start, dumps
            )
            loss[k] = max(-d["d_p_win_all"][me_i], 0.0)
        per_sim = np.where(who >= 0, loss[np.maximum(who, 0)], 0.0)
        shares = np.bincount(who[who >= 0], minlength=len(opp)) / len(who)
        if shares.max() > 0:
            k = int(shares.argmax())
            rival[pid] = (ctx.names.get(opp[k][0], opp[k][0]), float(shares[k]))
        o = np.argsort(PRICE_BUMP * mx, kind="stable")
        dist[pid] = PRICE_BUMP * mx
        deny[pid] = (o, per_sim[o])

    rows = []
    for r in gains.iter_rows(named=True):
        mx = np.sort(dist[r["player_id"]])
        dn = deny.get(r["player_id"])
        b, p_get, ev = best_bid(mx, r["d_p_win"], 0.0, my_faab, dn[1] if dn else None, cost_all)
        rv = rival.get(r["player_id"])
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
                "rival": rv[0] if rv else None,
                "rival_p": rv[1] if rv else None,
                "curve": bid_curve(mx, my_faab, b),
                "money_cost": float(cost_all[b]),
                "walk_away": faab_value.walk_away(
                    curve, my_faab, r["d_p_win"] + (float(dn[1].mean()) if dn else 0.0)
                ),
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
        0.0,
        my_faab,
        deny,
        st_dumps=dumps,
        curve=curve,
    )
    claim_model = bids.fit_claim_model(con, ctx.platform, ctx.season)
    chance = None
    if claim_model:
        recent = bids.weekly_ppr(con, ctx.season).filter(pl.col("week") >= ctx.week - 4)
        chance = bids.claim_chances(
            claim_model, vals, qual, ctx.slots, ctx.alive, ctx.faab, set(recent["player_id"])
        )
    return {
        "plan": plan,
        "market": market_outlook(vals, model, qual, opp, ctx.slots, ctx.names, chance),
        "summary": {
            "team": ctx.names[ctx.me],
            "faab": my_faab,
            "week": ctx.week,
            "chop_pending": ctx.chop_pending,
            "assume_chop": ctx.names.get(assume_chop) if assume_chop else None,
            "p_win": float(base["p_win"][me_i]),
            "p_chop_next": float(base["p_chop"][me_i, 0]),
            "cost_100": float(cost_all[min(100, my_faab)]),
            "cost_half": float(cost_all[my_faab // 2]),
            "sigma": sigma,
        },
        "sheet": sheet,
    }


def _group(claim: dict) -> str:
    """Claims sharing a drop form a group, of which at most one can land."""
    return claim["drop_id"] or claim["player_id"]


def plan_outcomes(chosen: list[dict]) -> dict[tuple[int, ...], float]:
    """{indices of the claims that land: probability}. Within a group only the first claim to win
    its bid lands: its drop is then gone, so Sleeper fails the rest of the group at no charge."""
    from itertools import product

    out: dict[tuple[int, ...], float] = {}
    for wins in product((True, False), repeat=len(chosen)):
        prob = float(np.prod([c["p_get"] if w else 1 - c["p_get"]
                              for c, w in zip(chosen, wins, strict=True)]))  # fmt: skip
        seen: set[str] = set()
        landed = []
        for i, (c, w) in enumerate(zip(chosen, wins, strict=True)):
            if w and _group(c) not in seen:
                seen.add(_group(c))
                landed.append(i)
        out[tuple(landed)] = out.get(tuple(landed), 0.0) + prob
    return out


def claim_plan(
    sheet, vals, ctx, gain_weeks, mu, me_i, sigma, noise, start, dist, lam, faab, deny=None,
    max_claims: int = MAX_CLAIMS, pool: int = PLAN_POOL, st_dumps=None, curve=None,
) -> pl.DataFrame:  # fmt: skip
    """Greedy joint plan. Each step re-values every remaining candidate given the claims already
    chosen: averaged over which of them land (their P(get)), using the roster those wins produce.
    A candidate either takes a drop no earlier claim uses or backs up an earlier claim by sharing
    its drop, and then only counts when that claim misses. Sleeper runs the highest bid first and
    ties went both ways in league history, so a backup bids strictly less than its group. Since
    only one claim per group can be charged, the plan's total is the top bid of each group, which
    stays within FAAB."""
    cands = sheet.head(pool).select("player_id", "player", "pos").rows()
    pts_cols = [f"pts_w{w}" for w in gain_weeks]
    played = {x[0]: x[1:] for x in vals.select("player_id", *pts_cols).iter_rows()}
    span = mu.shape[1] - start
    away = (
        dict(sheet.select("player_id", "walk_away").iter_rows())
        if "walk_away" in sheet.columns
        else {}
    )
    chosen: list[dict] = []
    for _ in range(max_claims):
        tops: dict[str, int] = {}
        for c in chosen:
            tops[_group(c)] = max(tops.get(_group(c), 0), c["bid"])
        budget = faab - sum(tops.values())
        outcomes = plan_outcomes(chosen)
        used = {c["drop_id"] for c in chosen}
        best = None
        for pid, name, pos in cands:
            if any(c["player_id"] == pid for c in chosen):
                continue
            for shared in (None, *sorted(d for d in used if d)):
                limit = (
                    min(c["bid"] for c in chosen if c["drop_id"] == shared) - 1
                    if shared
                    else budget
                )
                if limit <= 0:
                    continue
                d_win = reached = 0.0
                cost = None if curve is None else np.zeros(limit + 1)
                for landed, prob in outcomes.items():
                    won = [chosen[i] for i in landed]
                    drops = [c["drop_id"] for c in won]
                    if shared and shared in drops:
                        continue  # its drop is gone, so this claim fails
                    gains, _ = fit.claim_gain(
                        vals, ctx.me, ctx.slots, gain_weeks, pid,
                        [c["player_id"] for c in won], drops, used, shared,
                    )  # fmt: skip
                    # Base team strength in this outcome includes the earlier wins' gains.
                    bumped = mu.copy()
                    for c in won:
                        bumped[me_i, start:] += c["gains"]
                    d = survival.pickup_delta(
                        bumped,
                        me_i,
                        fit.season_gains(gains, played[pid], span),
                        sigma,
                        noise,
                        start,
                        st_dumps,
                    )
                    d_win += prob * d["d_p_win"]
                    reached += prob
                    if curve is not None:
                        left = faab - sum(c["bid"] for c in won)
                        cost += prob * faab_value.money_cost(curve, left, limit)
                if reached <= 0:
                    continue
                d_win /= reached  # value if this claim lands; the bid only matters when reached
                if curve is not None:
                    cost /= reached
                dn = (deny or {}).get(pid)
                b, p_get, ev = best_bid(
                    np.sort(dist[pid]), d_win, lam, limit, dn[1] if dn else None, cost
                )
                ev *= reached
                if b > 0 and ev > 0 and (best is None or ev > best["ev"]):
                    g0, drop0 = fit.claim_gain(
                        vals, ctx.me, ctx.slots, gain_weeks, pid, no_drop=used, drop=shared
                    )
                    best = {"player_id": pid, "player": name, "pos": pos, "bid": b,
                            "p_get": p_get, "ev": ev, "d_p_win": d_win, "drop_id": drop0,
                            "gains": fit.season_gains(g0, played[pid], span)}  # fmt: skip
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
                # The earlier claim(s) this one only runs behind, or None if it stands alone.
                "walk_away": away.get(c["player_id"]),
                "backup_for": ", ".join(e["player"] for e in chosen[:i] if _group(e) == _group(c))
                or None,
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
            "walk_away": pl.Int64,
            "backup_for": pl.Utf8,
        },  # fmt: skip
    )
