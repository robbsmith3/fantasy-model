"""Survival lineup: start the players that maximize P(we are not the lowest score this week),
which is the guillotine objective, instead of maximizing expected points.

When we're comfortably above the field, steadier players beat boom-or-bust ones with a slightly
higher mean; when we're projected near the bottom, variance helps. Outcomes come from
player_sim (empirical shapes, DNP mass, QB/pass-catcher correlation); opponents are assumed to
start their best projected lineups, and everyone is drawn jointly, so a shared NFL team links
our players with theirs.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fantasy_model import fit, player_sim, value

# Display order for starting slots; flex slots come after the positions they draw from.
SLOT_ORDER = ["QB", "RB", "WR", "TE", "WRRB_FLEX", "REC_FLEX", "FLEX", "SUPER_FLEX", "K", "DEF"]


def assign_slots(starters: list[tuple[str, str, float]], slots: list[str]) -> list[tuple[str, str]]:
    """(slot, id) for a legal set of (id, position, mu) starters, in SLOT_ORDER. Narrowest slots
    fill first with the highest projection, so flex slots get the leftovers."""
    used: set[str] = set()
    out = []
    starting = [s for s in slots if s in fit.SLOT_ELIGIBLE]
    for slot in sorted(starting, key=lambda s: len(fit.SLOT_ELIGIBLE[s])):
        for pid, pos, _ in sorted(starters, key=lambda r: -r[2]):
            if pid not in used and pos in fit.SLOT_ELIGIBLE[slot]:
                used.add(pid)
                out.append((slot, pid))
                break
    rank = {s: i for i, s in enumerate(SLOT_ORDER)}
    return sorted(out, key=lambda r: rank.get(r[0], len(rank)))


def legal(starters: list[tuple[str, str]], slots: list[str]) -> bool:
    """Can these (id, position) starters fill the starting slots exactly? Bipartite matching."""
    open_slots = [s for s in slots if s in fit.SLOT_ELIGIBLE]
    if len(starters) != len(open_slots):
        return False
    match: dict[int, int] = {}  # slot index -> starter index

    def assign(i: int, seen: set[int]) -> bool:
        for j, s in enumerate(open_slots):
            if j in seen or starters[i][1] not in fit.SLOT_ELIGIBLE[s]:
                continue
            seen.add(j)
            if j not in match or assign(match[j], seen):
                match[j] = i
                return True
        return False

    return all(assign(i, set()) for i in range(len(starters)))


def search(
    draws: np.ndarray,
    ids: list[str],
    pos: dict[str, str],
    mu: dict[str, float],
    line: np.ndarray,
    slots: list[str],
    max_iters: int = 10,
) -> tuple[list[str], list[str]]:
    """(max-points starters, survival starters). draws: (n_sims, len(ids)) outcomes for our
    roster; line: (n_sims,) score we must beat. Best-improvement single swaps from max points."""
    col = {p: j for j, p in enumerate(ids)}

    def p_safe(starters):
        return float((draws[:, [col[p] for p in starters]].sum(axis=1) > line).mean())

    max_pts = fit.best_lineup_ids([(p, pos[p], mu[p]) for p in ids], slots)[1]
    cur, cur_p = list(max_pts), p_safe(max_pts)
    for _ in range(max_iters):
        best = None
        bench = [p for p in ids if p not in cur and mu[p] > 0]
        for s in cur:
            for b in bench:
                cand = [b if x == s else x for x in cur]
                if not legal([(p, pos[p]) for p in cand], slots):
                    continue
                pc = p_safe(cand)
                if pc > cur_p + 1e-4 and (best is None or pc > best[1]):
                    best = (cand, pc)
        if best is None:
            break
        cur, cur_p = best
    return max_pts, cur


def optimize(
    con, ctx, week: int, team: str | None = None, n_sims: int = 20000, seed: int = 0
) -> dict:
    """Max-points vs survival lineup for `team` (default: ours) in `week`."""
    team = team or ctx.me
    vals = fit.adjust_for_injury(
        value.player_values(con, ctx.platform, ctx.league_id, [week]), [week]
    ).rename({f"pts_w{week}": "mu"})
    mine = vals.filter(pl.col("owner_team_id") == team)
    opp_starters: dict[str, list[str]] = {}
    for t in ctx.alive:
        if t == team:
            continue
        roster = vals.filter(pl.col("owner_team_id") == t)
        opp_starters[t] = fit.best_lineup_ids(
            list(roster.select("player_id", "position", "mu").iter_rows()), ctx.slots
        )[1]
    ids = mine["player_id"].to_list()
    every = ids + [p for ps in opp_starters.values() for p in ps]
    pool = vals.filter(pl.col("player_id").is_in(every)).unique("player_id")
    order = {pid: j for j, pid in enumerate(pool["player_id"].to_list())}
    draws = player_sim.sample_players(
        pool.select("position", "nfl_team", "mu"),
        player_sim.load_bank(con),
        n_sims,
        np.random.default_rng(seed),
    )
    opp = np.stack([draws[:, [order[p] for p in ps]].sum(axis=1) for ps in opp_starters.values()])
    line = opp.min(axis=0)  # we survive the week if we beat the lowest opponent

    pos = dict(mine.select("player_id", "position").iter_rows())
    name = dict(mine.select("player_id", "full_name").iter_rows())
    mu = dict(mine.select("player_id", "mu").iter_rows())
    mine_draws = draws[:, [order[p] for p in ids]]
    max_pts, surv = search(mine_draws, ids, pos, mu, line, ctx.slots)

    def p_safe(starters):
        return float((mine_draws[:, [ids.index(p) for p in starters]].sum(axis=1) > line).mean())

    def describe(starters):
        slotted = assign_slots([(p, pos[p], mu[p]) for p in starters], ctx.slots)
        return [
            {"slot": s, "player_id": p, "name": name[p], "pos": pos[p], "mu": round(mu[p], 1)}
            for s, p in slotted
        ]

    def summary(starters):
        return {
            "starters": describe(starters),
            "p_safe": p_safe(starters),
            "mean": round(sum(mu[p] for p in starters), 1),
        }

    return {
        "week": week,
        "team": team,
        "max_points": summary(max_pts),
        "survival": summary(surv),
        "swaps": [
            {"out": name[o], "in": name[i]}
            for o, i in zip(
                [p for p in max_pts if p not in surv],
                [p for p in surv if p not in max_pts],
                strict=True,
            )
        ],
        "line_median": round(float(np.median(line)), 1),
    }
