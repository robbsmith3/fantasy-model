"""End-to-end jobs against mocked Sleeper + Yahoo responses (no network)."""

import json
import time

import pytest
from conftest import FakeResp

from fantasy_model import cli, espn, fantasypros, sleeper, yahoo

# ---------------------------------------------------------------- Sleeper fixtures
SLEEPER = {
    "/state/nfl": {"season": "2026", "week": 9},
    "/users": [
        {"user_id": "u1", "display_name": "robb", "metadata": {"team_name": "Chop"}},
        {"user_id": "u2", "display_name": "x"},
    ],
    "/rosters": [
        {
            "roster_id": 1,
            "owner_id": "u1",
            "players": ["4046", "6794"],
            "starters": ["4046"],
            "settings": {"waiver_budget_used": 30, "fpts": 900, "fpts_decimal": 55},
        },
        {
            "roster_id": 2,
            "owner_id": "u2",
            "players": [],
            "starters": [],
            "settings": {"waiver_budget_used": 0},
        },
    ],
    "/matchups/": [
        {"roster_id": 1, "points": 120.5, "starters": ["4046"], "players_points": {"4046": 20.1}}
    ],
    "/transactions/": [
        {
            "transaction_id": "t1",
            "type": "waiver",
            "status": "complete",
            "roster_ids": [1],
            "adds": {"6794": 1},
            "drops": None,
            "settings": {"waiver_bid": 27},
            "leg": 9,
            "created": 1761800000000,
            "status_updated": 1761900000000,
        }
    ],
    "/players/nfl": {
        "4046": {"full_name": "QB One", "position": "QB", "team": "KC"},
        "6794": {
            "first_name": "WR",
            "last_name": "Two",
            "position": "WR",
            "team": "MIN",
            "injury_status": "Questionable",
        },
        "777": {"full_name": "Free Guy", "position": "RB", "team": "NYG"},
        "888": {"full_name": "Yahoo Rostered", "position": "TE", "team": "DAL"},
        "999": {"position": "OL", "team": "KC"},
    },
    "/league/": {
        "season": "2026",
        "name": "Guillo",
        "total_rosters": 18,
        "settings": {"waiver_budget": 100},
        "scoring_settings": {"rec": 0.5},
        "roster_positions": ["QB", "RB"],
        "previous_league_id": None,
    },
}


def sleeper_get(url, params=None, timeout=None):
    if "/projections/" in url:
        return FakeResp(
            [
                {
                    "player_id": "777",
                    "stats": {"rush_yd": 55},
                    "player": {"position": params["position[]"]},
                    "team": "NYG",
                }
            ],
            url,
        )
    for frag in (
        "/state/nfl",
        "/users",
        "/rosters",
        "/matchups/",
        "/transactions/",
        "/players/nfl",
        "/league/",
    ):
        if frag in url:
            return FakeResp(SLEEPER[frag], url)
    raise AssertionError(url)


# ------------------------------------------------ Yahoo fixtures (Yahoo's JSON shape)
LK = "461.l.1"
Y_PLAYER = lambda pid, extra: [
    [
        {"player_key": f"461.p.{pid}"},
        {"player_id": pid},
        {"name": {"full": f"P{pid}"}},
        [],
    ],
    extra,
]
Y_TEAM_META = lambda n, faab, me: [
    {"team_key": f"{LK}.t.{n}"},
    {"team_id": str(n)},
    {"name": f"Team{n}"},
    [],
    {"is_owned_by_current_login": 1 if me else 0},
    {"faab_balance": str(faab)},
    {"managers": [{"manager": {"guid": f"g{n}", "nickname": f"m{n}"}}]},
]
Y_LEAGUE_META = {
    "league_key": LK,
    "name": "YGuillo",
    "num_teams": 18,
    "season": "2026",
    "renew": "",
}

YAHOO = {
    "settings": {
        "league": [
            Y_LEAGUE_META,
            {
                "settings": [
                    {"uses_faab": "1", "roster_positions": [], "stat_modifiers": {"stats": []}}
                ]
            },
        ]
    },
    "teams/roster": {
        "league": [
            Y_LEAGUE_META,
            {
                "teams": {
                    "0": {
                        "team": [
                            Y_TEAM_META(1, 73, True),
                            {
                                "roster": {
                                    "coverage_type": "week",
                                    "0": {
                                        "players": {
                                            "0": {
                                                "player": Y_PLAYER(
                                                    "30001",
                                                    {
                                                        "selected_position": [
                                                            {"coverage_type": "week"},
                                                            {"position": "TE"},
                                                        ]
                                                    },
                                                )
                                            },
                                            "1": {
                                                "player": Y_PLAYER(
                                                    "30002",
                                                    {
                                                        "selected_position": [
                                                            {"coverage_type": "week"},
                                                            {"position": "BN"},
                                                        ]
                                                    },
                                                )
                                            },
                                            "count": 2,
                                        }
                                    },
                                }
                            },
                        ]
                    },
                    "1": {"team": [Y_TEAM_META(2, 100, False), {"roster": {"0": {"players": []}}}]},
                    "count": 2,
                }
            },
        ]
    },
    "teams/stats": {
        "league": [
            Y_LEAGUE_META,
            {
                "teams": {
                    "0": {"team": [Y_TEAM_META(1, 73, True), {"team_points": {"total": "131.24"}}]},
                    "count": 1,
                }
            },
        ]
    },
    "transactions": {
        "league": [
            Y_LEAGUE_META,
            {
                "transactions": {
                    "0": {
                        "transaction": [
                            {
                                "transaction_key": f"{LK}.tr.55",
                                "transaction_id": "55",
                                "type": "add/drop",
                                "status": "successful",
                                "timestamp": "1761900000",
                                "faab_bid": "41",
                            },
                            {
                                "players": {
                                    "0": {
                                        "player": Y_PLAYER(
                                            "30001",
                                            {
                                                "transaction_data": [
                                                    {
                                                        "type": "add",
                                                        "source_type": "waivers",
                                                        "destination_type": "team",
                                                        "destination_team_key": f"{LK}.t.1",
                                                    }
                                                ]
                                            },
                                        )
                                    },
                                    "1": {
                                        "player": Y_PLAYER(
                                            "30009",
                                            {
                                                "transaction_data": {
                                                    "type": "drop",
                                                    "source_type": "team",
                                                    "source_team_key": f"{LK}.t.1",
                                                    "destination_type": "waivers",
                                                }
                                            },
                                        )
                                    },
                                    "count": 2,
                                }
                            },
                        ]
                    },
                    "count": 1,
                }
            },
        ]
    },
}


def yahoo_get(url, params=None, timeout=None, headers=None):
    for frag in ("teams/roster", "teams/stats", "transactions", "settings"):
        if frag in url:
            return FakeResp({"fantasy_content": YAHOO[frag]}, url)
    raise AssertionError(url)


ESPN = {
    "players": [
        {
            "player": {
                "id": 3916387,
                "defaultPositionId": 3,
                "proTeamId": 33,
                "stats": [
                    {
                        "statSourceId": 1,
                        "statSplitTypeId": 1,
                        "seasonId": 2026,
                        "scoringPeriodId": 9,
                        "stats": {"53": 6.0, "42": 70.0},
                    }
                ],
            }
        }
    ]
}


def espn_get(url, params=None, headers=None, timeout=None):
    return FakeResp(ESPN, url)


@pytest.fixture
def mocked(con, monkeypatch):
    monkeypatch.setattr(sleeper.session, "get", sleeper_get)
    monkeypatch.setattr(espn.session, "get", espn_get)
    # FantasyPros comes from nflverse (network); unit-tested separately in test_sources.
    monkeypatch.setattr(fantasypros, "snapshot_projections", lambda con, sid, season: 0)
    monkeypatch.setattr(yahoo.session, "get", yahoo_get)
    yahoo._save_token(
        {
            "client_id": "c",
            "client_secret": "s",
            "refresh_token": "r",
            "access_token": "a",
            "expires_at": time.time() + 3600,
        }
    )
    # id map: yahoo 30001 -> sleeper 888 (30002 deliberately unmapped)
    con.execute("INSERT INTO player_id_map (sleeper_id, yahoo_id, name) VALUES ('888','30001','x')")
    return con


CFG = {
    "me": {"sleeper_user_id": "u1"},
    "projections": {"weeks_ahead": 1},
    "league": [{"platform": "sleeper", "league_id": "L1"}, {"platform": "yahoo", "league_id": LK}],
}


def count(con, t):
    return con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]


def test_all_jobs_and_idempotency(mocked):
    con = mocked
    for job in ("daily", "pre_waiver", "post_waiver", "pre_kickoff"):
        assert cli.run_job(job, CFG, con), job
    n_txn = count(con, "txn")
    assert cli.run_job("post_waiver", CFG, con)  # re-run must not duplicate
    assert count(con, "txn") == n_txn == 2
    assert count(con, "snapshot_run") >= 4


def test_bids_parsed_both_platforms(mocked):
    cli.run_job("post_waiver", CFG, mocked)
    bids = dict(mocked.execute("SELECT platform, bid FROM txn").fetchall())
    assert bids == {"sleeper": 27, "yahoo": 41}
    yp = mocked.execute(
        "SELECT action, team_id, source_type FROM txn_player WHERE platform='yahoo' ORDER BY action"
    ).fetchall()
    assert yp == [("add", f"{LK}.t.1", "waivers"), ("drop", f"{LK}.t.1", "team")]
    assert (
        mocked.execute("SELECT team_id FROM txn WHERE platform='yahoo'").fetchone()[0]
        == f"{LK}.t.1"
    )


def test_yahoo_rosters_state_and_scores(mocked):
    cli.run_job("pre_waiver", CFG, mocked)
    st = mocked.execute(
        "SELECT team_id, faab_remaining, n_players, eliminated "
        "FROM team_state_snapshot WHERE platform='yahoo' ORDER BY 1"
    ).fetchall()
    assert st == [(f"{LK}.t.1", 73, 2, False), (f"{LK}.t.2", 100, 0, True)]
    starters = dict(
        mocked.execute(
            "SELECT player_id, is_starter FROM roster_snapshot WHERE platform='yahoo'"
        ).fetchall()
    )
    assert starters == {"30001": True, "30002": False}
    assert mocked.execute("SELECT points FROM team_week WHERE platform='yahoo'").fetchone()[
        0
    ] == pytest.approx(131.24)
    assert (
        mocked.execute("SELECT is_me FROM team WHERE team_id=?", [f"{LK}.t.1"]).fetchone()[0]
        is True
    )


def test_waiver_pool_uses_id_map(mocked):
    cli.run_job("daily", CFG, mocked)
    cli.run_job("pre_waiver", CFG, mocked)
    pool = lambda p: {
        r[0]
        for r in mocked.execute(
            "SELECT DISTINCT sleeper_id FROM waiver_pool WHERE platform=?", [p]
        ).fetchall()
    }
    assert pool("sleeper") == {"777", "888"}  # 4046/6794 rostered, OL excluded
    assert pool("yahoo") == {"4046", "6794", "777"}  # 888 rostered via yahoo 30001


def test_one_league_failing_is_isolated(mocked, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(yahoo, "sync_league", boom)
    ok = cli.run_job("pre_waiver", CFG, mocked)
    assert ok is False
    assert count(mocked, "projection_snapshot") > 0  # other steps still ran
    err = mocked.execute("SELECT error FROM snapshot_run WHERE NOT ok").fetchone()[0]
    assert "yahoo down" in err


def test_raw_landed_before_parse(mocked):
    cli.run_job("post_waiver", CFG, mocked)
    eps = {r[0] for r in mocked.execute("SELECT DISTINCT endpoint FROM raw_response").fetchall()}
    assert {"league.transactions", "league.settings", "league.teams.roster"} <= eps
    body = mocked.execute(
        "SELECT body FROM raw_response WHERE platform='yahoo' AND endpoint='league.transactions'"
    ).fetchone()[0]
    assert (
        "faab_bid"
        in json.loads(body)["fantasy_content"]["league"][1]["transactions"]["0"]["transaction"][0]
    )
