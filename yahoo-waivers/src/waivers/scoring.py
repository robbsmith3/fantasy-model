"""Fixed-rule scoring. Every number here is a hand-set constant: nothing is fitted, tuned or
evaluated on Yahoo data (agreement). Inputs are public nflverse signals plus my league's settings,
roster and pool, used in memory for this run only."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from waivers.public_data import Signals, team_code
from waivers.yahoo_parse import LeagueInfo, Move, Player

HORIZON = 3  # weeks of value counted: next week plus two
RECENT_WEIGHT = 0.6  # recent games vs the season, in the base projection
USAGE_WEIGHT = 0.5  # how much a usage trend moves the projection
TREND_CAP = 0.3
RZ_POINTS = 0.15  # per recent red-zone target or carry per game
NO_DATA = {"K": 7.0, "DEF": 7.0}  # no public usage signals; same for roster and pool
NO_DATA_OTHER = 3.0
# Share of a week a status leaves him playing: (this week, next week, after)
INJURY = {
    "O": (0.0, 0.5, 1.0), "Out": (0.0, 0.5, 1.0),
    "D": (0.25, 0.9, 1.0), "Doubtful": (0.25, 0.9, 1.0),
    "Q": (0.85, 1.0, 1.0), "Questionable": (0.85, 1.0, 1.0),
    "IR": (0.0, 0.0, 0.0), "IR-R": (0.0, 0.0, 0.0), "PUP-R": (0.0, 0.0, 0.0),
    "NA": (0.0, 0.0, 0.0), "SUSP": (0.0, 0.0, 0.0),
}  # fmt: skip
FLEX = {
    "W/R/T": {"WR", "RB", "TE"}, "W/R": {"WR", "RB"}, "W/T": {"WR", "TE"}, "R/T": {"RB", "TE"},
    "Q/W/R/T": {"QB", "WR", "RB", "TE"},
}  # fmt: skip
INACTIVE_SLOTS = {"BN", "IR", "IR+", "NA"}
# FAAB range as a share of my balance, by weekly lineup gain (points): (min gain, low, high)
FAAB_TIERS = [(5.0, 0.20, 0.35), (3.0, 0.10, 0.20), (1.5, 0.04, 0.10), (0.5, 0.01, 0.04)]
LATE_SEASON_BOOST = 0.5  # later chops: a bigger share of what's left, since fewer chances remain
CHOP_WINDOW_DAYS, CHOP_MIN_DROPS = 8, 5


@dataclass
class Rec:
    priority: int
    player: Player
    drop: Player | None
    gain_per_week: float
    bid_low: int
    bid_high: int
    rationale: str


def chopped_releases(moves: list[Move], now_ts: int) -> set[str]:
    """Players dropped by a team that dropped at least CHOP_MIN_DROPS in the window: a chop."""
    recent = [
        m for m in moves if m.kind == "drop" and now_ts - m.timestamp <= CHOP_WINDOW_DAYS * 86400
    ]
    big = {t for t, n in Counter(m.team_key for m in recent).items() if t and n >= CHOP_MIN_DROPS}
    return {m.yahoo_id for m in recent if m.team_key in big}


def _availability(p: Player, sig: Signals | None, k: int) -> float:
    status = p.status or (sig.report if sig else None)
    curve = INJURY.get(status or "", (1.0, 1.0, 1.0))
    return curve[min(k, 2)]


def project(p: Player, sig: Signals | None, week: int, k: int, byes: dict, opps: dict,
            matchup: dict) -> float:  # fmt: skip
    """Projected points in week `week + k`."""
    team = (sig.team if sig else None) or team_code(p.team)
    if team and byes.get(team) == week + k:
        return 0.0
    if sig is None or sig.games == 0:
        base = NO_DATA.get(p.position, NO_DATA_OTHER)
    else:
        w = RECENT_WEIGHT if sig.games > 3 else 1.0
        base = w * sig.ppg_recent + (1 - w) * sig.ppg
        trend = _trend(sig)
        base = base * (1 + USAGE_WEIGHT * trend) + RZ_POINTS * sig.rz_recent
    opp = opps.get(week + k, {}).get(team) if team else None
    factor = matchup.get((opp, p.position), 1.0) if opp else 1.0
    return max(base * factor, 0.0) * _availability(p, sig, k)


def _trend(sig: Signals) -> float:
    parts = [(sig.opps_recent + 1) / (sig.opps + 1) - 1]
    if sig.snap and sig.snap_recent is not None:
        parts.append(sig.snap_recent / sig.snap - 1)
    return max(-TREND_CAP, min(TREND_CAP, sum(parts) / len(parts)))


def best_lineup(players: list[tuple[str, str, float]], slots: list[str]) -> float:
    """Points of the best starting lineup from (id, position, points); narrow slots fill first."""
    active = [s for s in slots if s not in INACTIVE_SLOTS]
    order = sorted(active, key=lambda s: len(FLEX.get(s, {s})))
    left = sorted(players, key=lambda x: -x[2])
    total = 0.0
    for slot in order:
        ok = FLEX.get(slot, {slot})
        for i, (_, pos, pts) in enumerate(left):
            if pos in ok:
                total += pts
                del left[i]
                break
    return total


def faab_range(gain_per_week: float, faab: int, week: int, last_week: int = 17) -> tuple[int, int]:
    boost = 1 + LATE_SEASON_BOOST * min(week / last_week, 1.0)
    for min_gain, low, high in FAAB_TIERS:
        if gain_per_week >= min_gain:
            return (min(faab, round(low * boost * faab)), min(faab, round(high * boost * faab)))
    return (0, min(faab, 1))


def _rationale(p: Player, sig: Signals | None, gain: float, drop: Player | None,
               chopped: bool, byes: dict, week: int) -> str:  # fmt: skip
    bits = [f"+{gain:.1f} pts/wk to your lineup"]
    if chopped:
        bits.append("released by a chopped team")
    if sig and sig.snap and sig.snap_recent is not None and sig.snap_recent - sig.snap >= 0.08:
        bits.append(f"snap share up to {sig.snap_recent:.0%} (season {sig.snap:.0%})")
    elif sig and sig.opps_recent - sig.opps >= 2:
        bits.append(f"{sig.opps_recent:.1f} targets+carries/game lately (season {sig.opps:.1f})")
    if sig and sig.rz_recent >= 2:
        bits.append(f"{sig.rz_recent:.1f} red-zone looks/game")
    if drop:
        dteam = team_code(drop.team)
        if dteam and byes.get(dteam) == week:
            bits.append(f"covers {drop.name}'s bye")
    if p.status:
        bits.append(f"status {p.status}")
    return "; ".join(bits[:4])


def recommend(
    info: LeagueInfo, roster: list[Player], pool: list[Player], faab: int,
    sigs: dict[str, Signals], byes: dict, opps: dict, matchup: dict, chopped: set[str],
    top: int = 5,
) -> list[Rec]:  # fmt: skip
    week = info.current_week
    weeks = range(HORIZON)

    def row(p):
        return [project(p, sigs.get(p.yahoo_id), week, k, byes, opps, matchup) for k in weeks]

    mine = {p.yahoo_id: (p, row(p)) for p in roster}
    active_slots = [s for s in info.slots if s not in {"IR", "IR+", "NA"}]
    on_active = [p for p in roster if p.slot not in {"IR", "IR+", "NA"}]
    open_spot = len(on_active) < len(active_slots)
    droppable = [None] if open_spot else [p for p in on_active]

    def lineup(ids_pts, k):
        return best_lineup([(i, pos, pts[k]) for i, pos, pts in ids_pts], info.slots)

    current = [
        (i, p.position, pts) for i, (p, pts) in mine.items() if p.slot not in {"IR", "IR+", "NA"}
    ]
    base = [lineup(current, k) for k in weeks]
    recs = []
    owned = set(mine)
    for p in pool:
        if p.yahoo_id in owned:
            continue
        pts = row(p)
        best = None
        for d in droppable:
            team = [x for x in current if d is None or x[0] != d.yahoo_id] + [
                (p.yahoo_id, p.position, pts)
            ]
            gain = sum(lineup(team, k) - base[k] for k in weeks)
            if best is None or gain > best[0]:
                best = (gain, d)
        if best and best[0] > 0.05:
            recs.append((best[0], p, best[1]))
    recs.sort(key=lambda r: -r[0])
    out = []
    for i, (gain, p, d) in enumerate(recs[:top], start=1):
        g = gain / HORIZON
        lo, hi = faab_range(g, faab, week) if info.uses_faab else (0, 0)
        sig = sigs.get(p.yahoo_id)
        out.append(Rec(i, p, d, g, lo, hi,
                       _rationale(p, sig, g, d, p.yahoo_id in chopped, byes, week)))  # fmt: skip
    return out
