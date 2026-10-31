"""ESPN projections adapter (public, UNOFFICIAL read endpoint; no auth; personal use).

One request returns every player's weekly projections (statSourceId=1) and actuals (=0) for the
whole season, past weeks included, as numeric stat ids. We map the ids we score to Sleeper stat
keys so league scoring applies unchanged (design rule 3), and keep the raw payload.
"""

from __future__ import annotations

import json
import time

import requests

from fantasy_model.db import land_raw, upsert

URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
    "/segments/0/leaguedefaults/3"
)
PLATFORM = "espn"
PROJECTED, ACTUAL = 1, 0

# ESPN stat id -> Sleeper stat key. Checked 2026-10-31: rescoring 1,196 ESPN projections with
# these keys reproduces ESPN's own appliedTotal within 0.31 pts (mean diff -0.01).
STAT_MAP = {
    "0": "pass_att",
    "1": "pass_cmp",
    "3": "pass_yd",
    "4": "pass_td",
    "19": "pass_2pt",
    "20": "pass_int",
    "23": "rush_att",
    "24": "rush_yd",
    "25": "rush_td",
    "26": "rush_2pt",
    "42": "rec_yd",
    "43": "rec_td",
    "44": "rec_2pt",
    "53": "rec",
    "58": "rec_tgt",
    "68": "fum",
    "72": "fum_lost",
}
POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE"}  # 5 = K, 16 = D/ST (not mapped yet)
PRO_TEAMS = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB",
    10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE",
    18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF",
    26: "SEA", 27: "TB", 28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}  # fmt: skip

session = requests.Session()
session.headers["User-Agent"] = "fantasy-model/0.1 (personal use)"


def fetch_players(con, snapshot_id: str, season: int, limit: int = 1500) -> list[dict]:
    flt = {"players": {"limit": limit, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    url = URL.format(season=season)
    for attempt in range(3):
        try:
            r = session.get(
                url,
                params={"view": "kona_player_info"},
                headers={"X-Fantasy-Filter": json.dumps(flt)},
                timeout=60,
            )
            body = r.json() if r.ok else None
            land_raw(con, snapshot_id, PLATFORM, "kona_player_info", r.url, r.status_code, body)
            r.raise_for_status()
            return body["players"]
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    return []


def parse(players: list[dict], season: int, source_id: int = PROJECTED, weeks=None):
    """Yield (espn_id, week, position, nfl_team, sleeper-keyed stats) per player-week."""
    for p in players:
        pl = p.get("player") or {}
        pos = POSITIONS.get(pl.get("defaultPositionId"))
        if not pos:
            continue
        for s in pl.get("stats") or []:
            wk = s.get("scoringPeriodId")
            if (
                s.get("statSourceId") != source_id
                or s.get("statSplitTypeId") != 1
                or s.get("seasonId") != season
                or not wk
                or (weeks is not None and wk not in weeks)
            ):
                continue
            stats = {STAT_MAP[k]: v for k, v in (s.get("stats") or {}).items() if k in STAT_MAP}
            yield str(pl["id"]), wk, pos, PRO_TEAMS.get(pl.get("proTeamId")), stats


def snapshot_projections(con, snapshot_id: str, season: int, weeks: list[int]) -> int:
    rows = [
        {
            "snapshot_id": snapshot_id,
            "source": PLATFORM,
            "season": season,
            "week": wk,
            "source_player_id": pid,
            "position": pos,
            "nfl_team": team,
            "stats": stats,
        }
        for pid, wk, pos, team, stats in parse(
            fetch_players(con, snapshot_id, season), season, PROJECTED, set(weeks)
        )
    ]
    return upsert(con, "projection_snapshot", rows)
