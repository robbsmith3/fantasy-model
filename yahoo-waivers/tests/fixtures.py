"""Synthetic data only: invented players, teams and stats in Yahoo's JSON shapes and nflverse's
table shapes. Nothing here came from Yahoo."""

from __future__ import annotations

import polars as pl

from waivers.public_data import PublicData

LK = "nfl.l.111"
TK = f"{LK}.t.3"
MARKER = "SECRET-BODY-MARKER-42"  # must never reach output or logs

# yahoo_id: (name, team, position, roster slot or None if available, gsis, pfr)
PLAYERS = {
    "1": ("Alpha Quarterback", "AAA", "QB", "QB", "G1", "P1"),
    "2": ("Bravo Runner", "AAA", "RB", "RB", "G2", "P2"),
    "3": ("Charlie Runner", "BBB", "RB", "RB", "G3", "P3"),
    "4": ("Delta Wideout", "BBB", "WR", "WR", "G4", "P4"),
    "5": ("Echo Wideout", "CCC", "WR", "WR", "G5", "P5"),
    "6": ("Foxtrot Tightend", "CCC", "TE", "TE", "G6", "P6"),
    "7": ("Golf Backup", "DDD", "WR", "BN", "G7", "P7"),
    "8": ("Hotel Backup", "DDD", "RB", "BN", "G8", "P8"),
    "9": ("India Hurt", "AAA", "RB", "IR", "G9", "P9"),
    "10": ("Nova Bench", "BBB", "WR", "BN", "G10", "P10"),
    "11": ("Oscar Bench", "CCC", "TE", "BN", "G11", "P11"),
    "20": ("Juliet Rising", "EEE", "WR", None, "G20", "P20"),
    "21": ("Kilo Steady", "EEE", "RB", None, "G21", "P21"),
    "22": ("Lima Passer", "FFF", "QB", None, "G22", "P22"),
    "23": ("Mike Blocker", "FFF", "TE", None, "G23", "P23"),
}
# Weekly points by player (weeks 1-9); Juliet's role grew in the last three games.
PPG = {"1": 18, "2": 12, "3": 10, "4": 11, "5": 9, "6": 7, "7": 3, "8": 2, "9": 14,
       "10": 4, "11": 1, "20": 6, "21": 5, "22": 15, "23": 2}  # fmt: skip


def _meta(pid, status=None):
    name, team, pos, *_ = PLAYERS[pid]
    m = [
        {"player_key": f"nfl.p.{pid}"},
        {"player_id": pid},
        {"name": {"full": name}},
        {"editorial_team_abbr": team},
        {"display_position": pos},
        {"primary_position": pos},
        {"eligible_positions": [{"position": pos}]},
        {"bye_weeks": {"week": "14"}},
    ]
    if status:
        m.append({"status": status})
    return m


def settings(uses_faab=True):
    slots = [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("W/R/T", 1), ("BN", 3), ("IR", 1)]
    mods = {4: "0.04", 5: "4", 6: "-2", 9: "0.1", 10: "6", 11: "1", 12: "0.1", 13: "6"}
    return {
        "league": [
            {"league_key": LK, "name": "Fake Guillotine", "current_week": 10},
            {"settings": [{
                "uses_faab": "1" if uses_faab else "0",
                "waiver_type": "FR",
                "roster_positions": [{"roster_position": {"position": p, "count": n}}
                                     for p, n in slots],
                "stat_modifiers": {"stats": [{"stat": {"stat_id": k, "value": v}}
                                             for k, v in mods.items()]},
            }]},
        ]
    }  # fmt: skip


def team():
    return {"team": [[{"team_key": TK}, {"team_id": "3"}, {"name": "Fake Team"},
                      {"faab_balance": "80"}]]}  # fmt: skip


def roster():
    mine = [pid for pid, v in PLAYERS.items() if v[3]]
    players = {
        str(i): {"player": [_meta(pid, "IR" if pid == "9" else None),
                            {"selected_position": [{"coverage_type": "week"},
                                                   {"position": PLAYERS[pid][3]}]}]}
        for i, pid in enumerate(mine)
    }  # fmt: skip
    players["count"] = len(mine)
    return {"team": [[{"team_key": TK}], {"roster": {"0": {"players": players}}}]}


def available(position):
    free = [pid for pid, v in PLAYERS.items() if v[3] is None and v[2] == position]
    players = {str(i): {"player": [_meta(pid)]} for i, pid in enumerate(free)}
    players["count"] = len(free)
    return {"league": [{"league_key": LK}, {"players": players}]}


def transactions(now_ts):
    # Team 9 was chopped: it dropped five players at once, Juliet among them.
    entries = {}
    dropped = ["20", "30", "31", "32", "33"]
    for i, pid in enumerate(dropped):
        entries[str(i)] = {"transaction": [
            {"transaction_key": f"t{i}", "type": "drop", "timestamp": str(now_ts - 3600)},
            {"players": {"0": {"player": [[{"player_id": pid},
                                           {"name": {"full": f"Dropped {pid}"}}],
                                          {"transaction_data": {"type": "drop",
                                                                "source_team_key": f"{LK}.t.9"}}]},
                         "count": 1}},
        ]}  # fmt: skip
    entries["count"] = len(dropped)
    return {"league": [{"league_key": LK}, {"transactions": entries}]}


def public() -> PublicData:
    rows, snaps, rz = [], [], []
    for pid, (_, team, pos, _slot, gsis, pfr) in PLAYERS.items():
        for w in range(1, 10):
            late = pid == "20" and w >= 7
            pts = PPG[pid] * (2.5 if late else 1.0)
            rows.append({"player_id": gsis, "team": team, "position": pos, "week": w,
                         "season_type": "REG", "opponent_team": "ZZZ",
                         "targets": 9 if late else 4, "carries": 2, "receptions": 3.0,
                         "receiving_yards": pts * 4, "receiving_tds": 0.0, "rushing_yards": 0.0,
                         "rushing_tds": 0.0, "passing_yards": 0.0, "passing_tds": 0.0,
                         "passing_interceptions": 0.0, "fantasy_points_ppr": pts})  # fmt: skip
            snaps.append({"pfr_player_id": pfr, "week": w, "offense_pct": 0.85 if late else 0.6})
            rz.append({"player_id": gsis, "week": w, "rz": 3 if late else 1})
    teams = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    sched = [{"week": w, "home_team": teams[i], "away_team": teams[i + 1]}
             for w in range(1, 18) for i in (0, 2, 4)]  # fmt: skip
    ids = [{"yahoo_id": float(pid), "gsis_id": v[4], "pfr_id": v[5]} for pid, v in PLAYERS.items()]
    return PublicData(
        stats=pl.DataFrame(rows),
        snaps=pl.DataFrame(snaps),
        redzone=pl.DataFrame(rz),
        ids=pl.DataFrame(ids),
        schedule=pl.DataFrame(sched),
        injuries=pl.DataFrame({"gsis_id": ["G5"], "week": [10], "report_status": ["Questionable"]}),
    )


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class FakeSession:
    """Answers Yahoo API and OAuth URLs from the fixtures above, recording every call."""

    def __init__(self, now_ts, script=None):
        self.now_ts, self.calls, self.script = now_ts, [], list(script or [])

    def post(self, url, auth=None, data=None, timeout=None):
        self.calls.append(("POST", url))
        return Resp(200, {"access_token": "acc", "refresh_token": "refresh-1"})

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url))
        if self.script:
            return self.script.pop(0)
        path = url.split("/fantasy/v2/", 1)[1]
        if path.endswith("/settings"):
            return Resp(200, {"fantasy_content": settings()})
        if path.endswith("/roster"):
            return Resp(200, {"fantasy_content": roster()})
        if path.startswith("team/"):
            return Resp(200, {"fantasy_content": team()})
        if "/transactions" in path:
            return Resp(200, {"fantasy_content": transactions(self.now_ts)})
        if "/players;" in path:
            pos = path.split("position=")[1].split(";")[0]
            return Resp(200, {"fantasy_content": available(pos)})
        raise AssertionError(f"unexpected request {path}")
