"""Roster fit: what a pickup adds to *my* weekly starting lineup, net of the player I'd drop.

Value to me is not a player's projected total: a 15-pt QB is worth ~0 behind an 18-pt QB, while a
9-pt RB who covers a bye week can be worth a lot. So for each week we pick the best legal lineup
and measure how much it improves with the candidate in (and the cheapest drop out)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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

# Availability by games after the status was reported (index 0 = the game it was reported for);
# the last value holds beyond the list. Projections ignore injury designations, so an Out player
# still shows a full-week projection.
# Questionable/Doubtful/Out are learned from nflverse injury reports 2018-2026 (`ffm
# injury-curves`): a fantasy-relevant player's chance of playing, relative to healthy players (who
# play 96% of their next games). 2018-22 and 2023-26 agree within ~0.05. The old hand-set guesses
# (Q 0.85; D 0.25 then 0.9; Out 0 then 0.8) had Out and Doubtful players back far too soon.
# Exception, Questionable at k=0: learned 0.72, but pre-game projections already drop players
# ruled inactive to 0, and on 2026 weeks 1-7 the remaining Questionable players' consensus was
# unbiased at 0.85 (bias -0.01 pts; 0.72: -1.46; none: +1.67, n=58). So same-week uses 0.85.
# IR/PUP players aren't on injury reports, so those stay hand-set.
INJURY_AVAIL: dict[str, list[float]] = {
    "Questionable": [0.85, 0.87, 0.92, 0.94],
    "Doubtful": [0.0, 0.48, 0.78, 0.83, 0.89, 0.90, 0.95],
    "Out": [0.0, 0.34, 0.56, 0.72, 0.78, 0.83, 0.85],
    "IR": [0.0, 0.0, 0.3, 0.5, 1.0],
    "PUP": [0.0, 0.0, 0.3, 0.5, 1.0],
    "NA": [0.0],
    "DNR": [0.0],
}
ET = ZoneInfo("America/New_York")


def availability(injury_status: str | None, k: int) -> float:
    curve = INJURY_AVAIL.get(injury_status or "")
    return curve[min(k, len(curve) - 1)] if curve else 1.0


def status_week(con, season: int, today: date | None = None) -> int:
    """The NFL week today's injury statuses describe. Game designations come out on Friday and
    stay until the next week's report, so from Friday (3 days before Monday night) through the
    following Thursday they belong to that week."""
    today = today or datetime.now(ET).date()
    rows = con.execute(
        "SELECT week, max(gameday) FROM nfl_schedules WHERE season = ? AND game_type = 'REG' "
        "GROUP BY week",
        [season],
    ).fetchall()
    done = [w for w, last in rows if date.fromisoformat(str(last)) - timedelta(days=3) <= today]
    return max(done, default=1)


def adjust_for_injury(values: pl.DataFrame, weeks: list[int], since: int) -> pl.DataFrame:
    """Scale each pts_w<N> by availability w - since games after the status (see status_week)
    and recompute ros_pts."""
    out = values.with_columns(
        pl.struct("injury_status", f"pts_w{w}")
        .map_elements(
            lambda r, k=k, w=w: r[f"pts_w{w}"] * availability(r["injury_status"], k),
            return_dtype=pl.Float64,
        )
        .alias(f"pts_w{w}")
        for w in weeks
        for k in [max(w - since, 0)]
    )
    return out.with_columns(pl.sum_horizontal([f"pts_w{w}" for w in weeks]).alias("ros_pts"))


def best_lineup(players: list[tuple[str, str, float]], slots: list[str]) -> float:
    """Max starting points for (id, position, points) players."""
    return best_lineup_ids(players, slots)[0]


def best_lineup_ids(
    players: list[tuple[str, str, float]], slots: list[str]
) -> tuple[float, list[str]]:
    """(points, starter ids). Fills the narrowest slots first, which is optimal for nested
    eligibility (QB ⊂ SUPER_FLEX, RB ⊂ FLEX, ...)."""
    starting = sorted((s for s in slots if s in SLOT_ELIGIBLE), key=lambda s: len(SLOT_ELIGIBLE[s]))
    pool = sorted(players, key=lambda p: -p[2])
    used: list[str] = []
    total = 0.0
    for slot in starting:
        for pid, pos, pts in pool:
            if pid not in used and pos in SLOT_ELIGIBLE[slot]:
                used.append(pid)
                total += max(pts, 0.0)
                break
    return total, used


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


def claim_gain(
    values: pl.DataFrame,
    team_id: str,
    slots: list[str],
    weeks: list[int],
    cand_id: str,
    adds: list[str] = (),
    drops: list[str] = (),
    no_drop: set[str] = frozenset(),
) -> tuple[list[float], str | None]:
    """Weekly lineup gain from claiming `cand_id` onto team_id's roster after `adds` joined and
    `drops` left (other claims that won), dropping the cheapest player not in `no_drop`.
    Returns (gains per week, drop_id)."""
    owned = values.filter(
        ((pl.col("owner_team_id") == team_id) & ~pl.col("player_id").is_in(list(drops)))
        | pl.col("player_id").is_in(list(adds))
    )
    cand = values.filter(pl.col("player_id") == cand_id).row(0, named=True)
    rows = {w: _week_players(owned, w) for w in weeks}
    base = {w: best_lineup(rows[w], slots) for w in weeks}
    best = None
    for drop_id in owned["player_id"]:
        if drop_id in no_drop or drop_id in adds:
            continue
        gains = []
        for w in weeks:
            lineup = [p for p in rows[w] if p[0] != drop_id]
            lineup.append((cand_id, cand["position"], cand[f"pts_w{w}"]))
            gains.append(best_lineup(lineup, slots) - base[w])
        if best is None or sum(gains) > sum(best[0]):
            best = (gains, drop_id)
    return best if best else ([0.0] * len(weeks), None)
