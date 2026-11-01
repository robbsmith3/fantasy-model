import time

from fastapi.testclient import TestClient

from fantasy_model import api


def test_async_cache_reports_computing_then_ready_and_keeps_errors():
    c = api.Cache(workers=1)
    assert c.get_async(("k",), lambda: (time.sleep(0.2), 42)[1])["status"] == "computing"
    for _ in range(50):
        r = c.get_async(("k",), lambda: 0)
        if r["status"] == "ready":
            break
        time.sleep(0.05)
    assert r == {"status": "ready", "data": 42}
    c.get_async(("bad",), lambda: 1 / 0)
    time.sleep(0.2)
    assert c.get_async(("bad",), lambda: 0)["status"] == "error"
    c.clear_errors()
    assert c.get_async(("bad",), lambda: 1)["status"] in {"computing", "ready"}


def test_spa_serves_assets_and_falls_back_to_index(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(api, "WEB_DIST", tmp_path)
    client = TestClient(api.create_app())
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert "root" in client.get("/waivers").text  # client-side route
    assert "root" in client.get("/../../etc/passwd").text  # no escaping the dist folder


def test_timeline_rolls_waiver_forward_in_eastern_time_and_finds_kickoffs(con, monkeypatch):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    # A main run (10+ claims) two weeks ago at 03:08 Eastern.
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    last = (
        (now - timedelta(days=14)).astimezone(et).replace(hour=3, minute=8, second=0, microsecond=0)
    )
    for i in range(10):
        con.execute(
            "INSERT INTO txn (platform, league_id, transaction_id, season, week, type, status,"
            " processed_at) VALUES ('sleeper','L',?,2026,1,'waiver','failed',?)",
            [f"t{i}", last],
        )
    day = (now + timedelta(days=2)).astimezone(et).date().isoformat()
    con.execute(
        f"""CREATE TABLE nfl_schedules AS SELECT 'REG' AS game_type, 9 AS week,
            '{day}' AS gameday, '13:00' AS gametime, 'Sunday' AS weekday"""
    )
    t = api.compute_timeline(con, "L")
    nw = datetime.fromisoformat(t["next_waiver"])
    assert now < nw <= now + timedelta(days=7)
    assert nw.astimezone(et).strftime("%H:%M") == "03:08"
    assert t["next_kickoff"] == t["sunday_main"] and t["next_kickoff_week"] == 9


def test_team_avatars_prefer_custom_team_image(con):
    import json

    users = [
        {"user_id": "u1", "avatar": "abc", "metadata": {"avatar": "https://x/custom.jpg"}},
        {"user_id": "u2", "avatar": "def", "metadata": {}},
    ]
    con.execute(
        "INSERT INTO raw_response VALUES ('s','2026-01-01','sleeper','league.users',"
        "'https://api.sleeper.app/v1/league/L/users',200,?)",
        [json.dumps(users)],
    )
    for team, owner in (("1", "u1"), ("2", "u2"), ("3", "u9")):
        con.execute(
            "INSERT INTO team (platform, league_id, team_id, owner_id) VALUES ('sleeper','L',?,?)",
            [team, owner],
        )
    assert api.team_avatars(con, "L") == {
        "1": "https://x/custom.jpg",
        "2": "https://sleepercdn.com/avatars/thumb/def",
    }
