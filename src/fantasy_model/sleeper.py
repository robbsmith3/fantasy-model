"""Sleeper adapter: fetch -> land raw -> parse into common schema.

Official API (read-only, no auth, <1000 calls/min). /players/nfl: max 1 call/day.
Projections endpoint is UNOFFICIAL; parsed defensively, raw kept.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import requests

from fantasy_model.db import land_raw, now_utc, upsert

BASE = "https://api.sleeper.app/v1"
PROJ_BASE = "https://api.sleeper.app/projections/nfl"
PLATFORM = "sleeper"
SKILL_POS = ["QB", "RB", "WR", "TE", "K", "DEF"]

session = requests.Session()
session.headers["User-Agent"] = "fantasy-model/0.1 (personal use)"


def _get(con, snapshot_id: str, endpoint: str, url: str, params=None, retries: int = 3):
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=30)
            body = r.json() if r.ok else None
            land_raw(con, snapshot_id, PLATFORM, endpoint, r.url, r.status_code, body)
            r.raise_for_status()
            return body
        except (requests.ConnectionError, requests.Timeout):
            if attempt == retries - 1:
                raise
            time.sleep(2**attempt)


def _ts(ms):
    return datetime.fromtimestamp(ms / 1000, tz=UTC) if ms else None


def nfl_state(con, snapshot_id: str) -> dict:
    return _get(con, snapshot_id, "state.nfl", f"{BASE}/state/nfl")


def sync_league(con, snapshot_id: str, league_id: str, my_user_id: str | None = None) -> None:
    lg = _get(con, snapshot_id, "league", f"{BASE}/league/{league_id}")
    settings = lg.get("settings") or {}
    upsert(
        con,
        "league",
        [
            {
                "platform": PLATFORM,
                "league_id": league_id,
                "season": int(lg["season"]),
                "name": lg.get("name"),
                "total_rosters": lg.get("total_rosters"),
                "faab_budget": settings.get("waiver_budget"),
                "scoring": lg.get("scoring_settings"),
                "roster_slots": lg.get("roster_positions"),
                "settings": settings,
                "previous_league_id": lg.get("previous_league_id"),
                "updated_at": now_utc(),
            }
        ],
    )

    users = _get(con, snapshot_id, "league.users", f"{BASE}/league/{league_id}/users") or []
    rosters = _get(con, snapshot_id, "league.rosters", f"{BASE}/league/{league_id}/rosters") or []
    by_id = {u["user_id"]: u for u in users}

    upsert(
        con,
        "team",
        [
            {
                "platform": PLATFORM,
                "league_id": league_id,
                "team_id": str(r["roster_id"]),
                "owner_id": r.get("owner_id"),
                "owner_name": (by_id.get(r.get("owner_id")) or {}).get("display_name"),
                "team_name": ((by_id.get(r.get("owner_id")) or {}).get("metadata") or {}).get(
                    "team_name"
                ),
                "is_me": bool(my_user_id) and r.get("owner_id") == my_user_id,
            }
            for r in rosters
        ],
    )

    budget = settings.get("waiver_budget")
    roster_rows, state_rows = [], []
    for r in rosters:
        tid = str(r["roster_id"])
        players = r.get("players") or []
        starters = set(r.get("starters") or [])
        s = r.get("settings") or {}
        used = s.get("waiver_budget_used")
        roster_rows += [
            {
                "snapshot_id": snapshot_id,
                "platform": PLATFORM,
                "league_id": league_id,
                "team_id": tid,
                "player_id": str(p),
                "is_starter": p in starters,
            }
            for p in players
        ]
        state_rows.append(
            {
                "snapshot_id": snapshot_id,
                "platform": PLATFORM,
                "league_id": league_id,
                "team_id": tid,
                "faab_remaining": budget - used
                if budget is not None and used is not None
                else None,
                "points_for": (s.get("fpts") or 0) + (s.get("fpts_decimal") or 0) / 100,
                "n_players": len(players),
                "eliminated": len(players) == 0,
            }
        )
    upsert(con, "roster_snapshot", roster_rows)
    upsert(con, "team_state_snapshot", state_rows)


def sync_week_scores(con, snapshot_id: str, league_id: str, season: int, week: int) -> int:
    rows = (
        _get(con, snapshot_id, "league.matchups", f"{BASE}/league/{league_id}/matchups/{week}")
        or []
    )
    return upsert(
        con,
        "team_week",
        [
            {
                "platform": PLATFORM,
                "league_id": league_id,
                "season": season,
                "week": week,
                "team_id": str(m["roster_id"]),
                "points": m.get("points"),
                "starters": m.get("starters"),
                "player_points": m.get("players_points"),
            }
            for m in rows
        ],
    )


def sync_transactions(con, snapshot_id: str, league_id: str, season: int, leg: int) -> int:
    txns = (
        _get(
            con, snapshot_id, "league.transactions", f"{BASE}/league/{league_id}/transactions/{leg}"
        )
        or []
    )
    t_rows, p_rows = [], []
    for t in txns:
        tid = str(t["transaction_id"])
        roster_ids = t.get("roster_ids") or []
        t_rows.append(
            {
                "platform": PLATFORM,
                "league_id": league_id,
                "transaction_id": tid,
                "season": season,
                "week": t.get("leg", leg),
                "type": t.get("type"),
                "status": t.get("status"),
                "team_id": str(roster_ids[0]) if roster_ids else None,
                "bid": (t.get("settings") or {}).get("waiver_bid"),
                "created_at": _ts(t.get("created")),
                "processed_at": _ts(t.get("status_updated")),
            }
        )
        src = "waivers" if t.get("type") == "waiver" else t.get("type")
        for action, mapping in (("add", t.get("adds")), ("drop", t.get("drops"))):
            for pid, rid in (mapping or {}).items():
                p_rows.append(
                    {
                        "platform": PLATFORM,
                        "league_id": league_id,
                        "transaction_id": tid,
                        "player_id": str(pid),
                        "action": action,
                        "team_id": str(rid),
                        "source_type": src,
                    }
                )
    upsert(con, "txn", t_rows)
    upsert(con, "txn_player", p_rows)
    return len(t_rows)


def snapshot_players(con, snapshot_id: str) -> int:
    """~5 MB payload. Schedule so this runs at most once per day."""
    players = _get(con, snapshot_id, "players.nfl", f"{BASE}/players/nfl") or {}
    rows = []
    for pid, p in players.items():
        if p.get("position") not in SKILL_POS:
            continue
        name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        rows.append(
            {
                "snapshot_id": snapshot_id,
                "platform": PLATFORM,
                "player_id": str(pid),
                "full_name": name,
                "position": p.get("position"),
                "nfl_team": p.get("team"),
                "status": p.get("status"),
                "injury_status": p.get("injury_status"),
                "injury_body_part": p.get("injury_body_part"),
                "practice_participation": p.get("practice_participation"),
                "depth_chart_order": p.get("depth_chart_order"),
            }
        )
    return upsert(con, "player_status_snapshot", rows)


def snapshot_projections(con, snapshot_id: str, season: int, week: int) -> int:
    rows = []
    for pos in SKILL_POS:
        try:
            data = _get(
                con,
                snapshot_id,
                f"projections.{pos}",
                f"{PROJ_BASE}/{season}/{week}",
                params={"season_type": "regular", "position[]": pos},
            )
        except requests.HTTPError:
            continue
        for item in data or []:
            pid = item.get("player_id")
            if not pid:
                continue
            player = item.get("player") or {}
            rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "source": PLATFORM,
                    "season": season,
                    "week": week,
                    "source_player_id": str(pid),
                    "position": player.get("position", pos),
                    "nfl_team": item.get("team") or player.get("team"),
                    "stats": item.get("stats") or {},
                }
            )
    return upsert(con, "projection_snapshot", rows)
