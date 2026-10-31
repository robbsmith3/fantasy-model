"""Roster fit: what a pickup adds to *my* weekly starting lineup, net of the player I'd drop.

Value to me is not a player's projected total: a 15-pt QB is worth ~0 behind an 18-pt QB, while a
9-pt RB who covers a bye week can be worth a lot. So for each week we pick the best legal lineup
and measure how much it improves with the candidate in (and the cheapest drop out)."""

from __future__ import annotations

import polars as pl

# Which positions may fill each Sleeper slot. Bench (BN) and IR slots never score.
SLOT_ELIGIBLE: dict[str, frozenset[str]] = {
    "QB": frozenset({"QB"}),
    "RB": frozenset({"RB"}),
    "WR": frozenset({"WR"}),
    "TE": frozenset({"TE"}),
    "K": frozenset({"K"}),
    "DEF": frozenset({"DEF"}),
    "FLEX": frozenset({"RB", "WR", "TE"}),
    "WRRB_FLEX": frozenset({"RB", "WR"}),
    "REC_FLEX": frozenset({"WR", "TE"}),
    "SUPER_FLEX": frozenset({"QB", "RB", "WR", "TE"}),
}

# Heuristic availability by weeks-from-now (index 0 = first projected week), per injury status.
# Projections ignore injury designations, so an IR player still shows a full-week projection.
INJURY_AVAIL: dict[str, list[float]] = {
    "Questionable": [0.85],
    "Doubtful": [0.25, 0.9],
    "Out": [0.0, 0.8],
    "IR": [0.0, 0.0, 0.3, 0.5],
    "PUP": [0.0, 0.0, 0.3, 0.5],
    "NA": [0.0] * 18,
    "DNR": [0.0] * 18,
}


def availability(injury_status: str | None, k: int) -> float:
    curve = INJURY_AVAIL.get(injury_status or "", [])
    return curve[k] if k < len(curve) else (curve[-1] if curve and curve[-1] == 0.0 else 1.0)


def adjust_for_injury(values: pl.DataFrame, weeks: list[int]) -> pl.DataFrame:
    """Scale each pts_w<N> by expected availability and recompute ros_pts."""
    out = values.with_columns(
        pl.struct("injury_status", f"pts_w{w}")
        .map_elements(
            lambda r, k=k, w=w: r[f"pts_w{w}"] * availability(r["injury_status"], k),
            return_dtype=pl.Float64,
        )
        .alias(f"pts_w{w}")
        for k, w in enumerate(weeks)
    )
    return out.with_columns(pl.sum_horizontal([f"pts_w{w}" for w in weeks]).alias("ros_pts"))


def best_lineup(players: list[tuple[str, str, float]], slots: list[str]) -> float:
    """Max starting points for (id, position, points) players. Fills the narrowest slots first,
    which is optimal for nested eligibility (QB ⊂ SUPER_FLEX, RB ⊂ FLEX, ...)."""
    starting = sorted((s for s in slots if s in SLOT_ELIGIBLE), key=lambda s: len(SLOT_ELIGIBLE[s]))
    pool = sorted(players, key=lambda p: -p[2])
    used: set[str] = set()
    total = 0.0
    for slot in starting:
        for pid, pos, pts in pool:
            if pid not in used and pos in SLOT_ELIGIBLE[slot]:
                used.add(pid)
                total += max(pts, 0.0)
                break
    return total


def _week_players(df: pl.DataFrame, week: int) -> list[tuple[str, str, float]]:
    return list(df.select("player_id", "position", f"pts_w{week}").iter_rows())


def lineup_points(values: pl.DataFrame, team_id: str, slots: list[str], weeks: list[int]):
    """{week: best projected starting points} for one team's current roster."""
    roster = values.filter(pl.col("owner_team_id") == team_id)
    return {w: best_lineup(_week_players(roster, w), slots) for w in weeks}


def roster_gain(
    values: pl.DataFrame,
    team_id: str,
    slots: list[str],
    weeks: list[int],
    candidates: int = 300,
) -> pl.DataFrame:
    """For the top `candidates` available players (by ros_pts), the lineup points they add to
    `team_id` each week after dropping whichever current player costs the least.

    Columns: player_id, full_name, position, nfl_team, injury_status, ros_pts,
    gain_w<N> per week, gain (total), drop_id, drop_name. Sorted by gain.
    """
    roster = values.filter(pl.col("owner_team_id") == team_id)
    pool = values.filter(pl.col("owner_team_id").is_null()).sort("ros_pts", descending=True)
    pool = pool.head(candidates)
    base = {w: best_lineup(_week_players(roster, w), slots) for w in weeks}
    rows_by_week = {w: _week_players(roster, w) for w in weeks}
    names = dict(roster.select("player_id", "full_name").iter_rows())

    out = []
    for cand in pool.iter_rows(named=True):
        best = None
        for drop_id in names:
            gains = []
            for w in weeks:
                lineup = [p for p in rows_by_week[w] if p[0] != drop_id]
                lineup.append((cand["player_id"], cand["position"], cand[f"pts_w{w}"]))
                gains.append(best_lineup(lineup, slots) - base[w])
            if best is None or sum(gains) > sum(best[1]):
                best = (drop_id, gains)
        drop_id, gains = best
        out.append(
            {
                **{k: cand[k] for k in ("player_id", "full_name", "position", "nfl_team")},
                "injury_status": cand["injury_status"],
                "ros_pts": cand["ros_pts"],
                **{f"gain_w{w}": round(g, 2) for w, g in zip(weeks, gains, strict=True)},
                "gain": round(sum(gains), 2),
                "drop_id": drop_id,
                "drop_name": names[drop_id],
            }
        )
    return pl.DataFrame(out).sort("gain", descending=True)
