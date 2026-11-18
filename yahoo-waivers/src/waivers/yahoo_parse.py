"""Yahoo's nested JSON -> plain dataclasses, in memory.

Yahoo wraps objects in lists of single-key dicts and collections in {"0": ..., "count": n} maps.
_merge and _items flatten those shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Yahoo stat ids in league scoring settings -> the nflverse player-stats column(s) they map to.
STAT_COLUMNS = {
    4: ("passing_yards",),
    5: ("passing_tds",),
    6: ("passing_interceptions",),
    9: ("rushing_yards",),
    10: ("rushing_tds",),
    11: ("receptions",),
    12: ("receiving_yards",),
    13: ("receiving_tds",),
    16: ("passing_2pt_conversions", "rushing_2pt_conversions", "receiving_2pt_conversions"),
    18: ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost"),
}


@dataclass
class Player:
    key: str
    yahoo_id: str
    name: str
    team: str | None
    position: str  # primary position (QB, RB, WR, TE, K, DEF)
    eligible: list[str] = field(default_factory=list)
    status: str | None = None  # Yahoo injury tag: Q, D, O, IR, PUP-R, NA...
    bye: int | None = None
    slot: str | None = None  # roster only: selected position (BN, IR, W/R/T...)


@dataclass
class LeagueInfo:
    name: str
    current_week: int
    slots: list[str]
    scoring: dict[int, float]  # Yahoo stat id -> points
    uses_faab: bool
    waiver_type: str | None
    faab_budget: int | None


@dataclass
class Move:
    kind: str  # "add" or "drop"
    yahoo_id: str
    name: str
    team_key: str | None  # source team for drops, destination for adds
    timestamp: int


def _merge(x) -> dict:
    """[{a: 1}, {b: 2}, [...]] -> {a: 1, b: 2}; nested lists flattened."""
    out: dict = {}
    if isinstance(x, dict):
        return dict(x)
    for item in x or []:
        if isinstance(item, dict):
            out.update(item)
        elif isinstance(item, list):
            out.update(_merge(item))
    return out


def _items(container, key: str) -> list:
    """{"0": {key: v}, "1": {key: v}, "count": 2} -> [v, v]."""
    if not isinstance(container, dict):
        return []
    return [v[key] for k, v in sorted(container.items()) if k.isdigit() and key in v]


def _player(raw) -> Player:
    """A Yahoo player entry: [[meta dicts...], {extra}, ...]."""
    meta = _merge(raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], list) else raw)
    extra = _merge(raw[1:]) if isinstance(raw, list) else {}
    eligible = [e["position"] for e in meta.get("eligible_positions", []) if "position" in e]
    bye = meta.get("bye_weeks", {}).get("week")
    slot = _merge(extra.get("selected_position", [])).get("position")
    return Player(
        key=meta.get("player_key", ""),
        yahoo_id=str(meta.get("player_id", "")),
        name=meta.get("name", {}).get("full", ""),
        team=(meta.get("editorial_team_abbr") or "").upper() or None,
        position=meta.get("primary_position") or meta.get("display_position", ""),
        eligible=eligible,
        status=meta.get("status") or None,
        bye=int(bye) if str(bye).isdigit() else None,
        slot=slot,
    )


def parse_settings(fc: dict) -> LeagueInfo:
    league = fc["league"]
    meta = _merge(league[0] if isinstance(league[0], list) else [league[0]])
    settings = _merge(league[1]["settings"])
    slots = []
    for rp in settings.get("roster_positions", []):
        p = rp.get("roster_position", rp)
        slots += [p["position"]] * int(p.get("count", 1))
    scoring = {}
    for s in (settings.get("stat_modifiers") or {}).get("stats", []):
        st = s.get("stat", s)
        try:
            scoring[int(st["stat_id"])] = float(st["value"])
        except (KeyError, ValueError):
            continue
    budget = settings.get("faab_budget") or settings.get("waiver_budget")
    return LeagueInfo(
        name=meta.get("name", ""),
        current_week=int(meta.get("current_week", 1)),
        slots=slots,
        scoring=scoring,
        uses_faab=str(settings.get("uses_faab", "0")) == "1",
        waiver_type=settings.get("waiver_type"),
        faab_budget=int(budget) if str(budget).isdigit() else None,
    )


def parse_faab(fc: dict) -> int | None:
    meta = _merge(fc["team"][0] if isinstance(fc["team"][0], list) else fc["team"])
    v = meta.get("faab_balance")
    return int(v) if str(v).lstrip("-").isdigit() else None


def parse_roster(fc: dict) -> list[Player]:
    team = fc["team"]
    roster = _merge(team[1:]).get("roster", {})
    players = roster.get("0", {}).get("players", roster.get("players", {}))
    return [_player(p) for p in _items(players, "player")]


def parse_players(fc: dict) -> list[Player]:
    league = fc["league"]
    players = _merge(league[1:]).get("players", {})
    return [_player(p) for p in _items(players, "player")]


def parse_transactions(fc: dict) -> list[Move]:
    league = fc["league"]
    out = []
    for t in _items(_merge(league[1:]).get("transactions", {}), "transaction"):
        tmeta = _merge(t[0] if isinstance(t[0], list) else [t[0]])
        ts = int(tmeta.get("timestamp", 0))
        players = _merge(t[1:]).get("players", {})
        for p in _items(players, "player"):
            meta = _merge(p[0])
            data = _merge(p[1].get("transaction_data", {})) if len(p) > 1 else {}
            kind = data.get("type")
            if kind not in ("add", "drop"):
                continue
            team = (
                data.get("source_team_key") if kind == "drop" else data.get("destination_team_key")
            )
            name = meta.get("name", {}).get("full", "")
            out.append(Move(kind, str(meta.get("player_id", "")), name, team, ts))
    return out
