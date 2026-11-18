"""End-to-end jobs against mocked Sleeper responses (no network)."""

import pytest
from conftest import FakeResp

from fantasy_model import cli, espn, fantasypros, odds, projection_model, sleeper

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
    monkeypatch.setattr(odds, "snapshot_props", lambda *a: 0)
    # Our model trains on nflverse tables (not in this fixture); tested in test_projection_model.
    monkeypatch.setattr(projection_model, "snapshot_projections", lambda *a: 0)
    # The weekly review needs nflverse tables too; tested in test_review / test_bids.
    monkeypatch.setattr(cli, "run_review", lambda *a: "skipped")
    return con


CFG = {
    "me": {"sleeper_user_id": "u1"},
    "projections": {"weeks_ahead": 1},
    "league": [{"platform": "sleeper", "league_id": "L1"}],
}


def count(con, t):
    return con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]


def test_all_jobs_and_idempotency(mocked):
    con = mocked
    for job in ("daily", "pre_waiver", "post_waiver", "pre_kickoff"):
        assert cli.run_job(job, CFG, con), job
    n_txn = count(con, "txn")
    assert cli.run_job("post_waiver", CFG, con)  # re-run must not duplicate
    assert count(con, "txn") == n_txn == 1
    assert count(con, "snapshot_run") >= 4


def test_bids_parsed(mocked):
    cli.run_job("post_waiver", CFG, mocked)
    assert dict(mocked.execute("SELECT platform, bid FROM txn").fetchall()) == {"sleeper": 27}


def test_waiver_pool(mocked):
    cli.run_job("daily", CFG, mocked)
    cli.run_job("pre_waiver", CFG, mocked)
    pool = lambda p: {
        r[0]
        for r in mocked.execute(
            "SELECT DISTINCT sleeper_id FROM waiver_pool WHERE platform=?", [p]
        ).fetchall()
    }
    assert pool("sleeper") == {"777", "888"}  # 4046/6794 rostered, OL excluded


def test_one_league_failing_is_isolated(mocked, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("sleeper down")

    monkeypatch.setattr(sleeper, "sync_league", boom)
    ok = cli.run_job("pre_waiver", CFG, mocked)
    assert ok is False
    assert count(mocked, "projection_snapshot") > 0  # other steps still ran
    err = mocked.execute("SELECT error FROM snapshot_run WHERE NOT ok").fetchone()[0]
    assert "sleeper down" in err


def test_raw_landed_before_parse(mocked):
    cli.run_job("post_waiver", CFG, mocked)
    eps = {r[0] for r in mocked.execute("SELECT DISTINCT endpoint FROM raw_response").fetchall()}
    assert {"league.transactions", "league", "league.rosters"} <= eps
    body = mocked.execute(
        "SELECT body FROM raw_response WHERE endpoint='league.transactions' LIMIT 1"
    ).fetchone()[0]
    assert "waiver_bid" in body
