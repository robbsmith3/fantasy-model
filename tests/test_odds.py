import math
from datetime import UTC, datetime

import pytest

from fantasy_model import db, odds


def _event(bookmakers):
    return {"id": "e1", "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills",
            "bookmakers": bookmakers}  # fmt: skip


def _book(markets):
    return {"key": "dk", "markets": [{"key": k, "outcomes": o} for k, o in markets.items()]}


def ou(player, point, over, under):
    return [
        {"name": "Over", "description": player, "price": over, "point": point},
        {"name": "Under", "description": player, "price": under, "point": point},
    ]


def test_norm_drops_punctuation_and_suffixes():
    assert odds.norm("D.J. Moore Jr.") == odds.norm("DJ Moore") == "djmoore"
    assert odds.norm("Kenneth Walker III") == "kennethwalker"


def test_summarize_takes_the_median_line_and_devigs():
    ev = _event([
        _book({"player_rush_yds": ou("Isiah Pacheco", 60.5, -110, -110)}),
        _book({"player_rush_yds": ou("Isiah Pacheco", 62.5, -110, -110)}),
        _book({"player_rush_yds": ou("Isiah Pacheco", 70.5, -110, -110)}),
    ])  # fmt: skip
    m = odds.summarize(ev)["Isiah Pacheco"]["player_rush_yds"]
    assert m["line"] == 62.5 and m["p_over"] == pytest.approx(0.5)


def test_stat_line_converts_tds_with_poisson_means():
    mk = {
        "player_pass_tds": {"line": 1.5, "p_over": 0.5},
        "player_anytime_td": {"p_yes": 0.4},
        "player_pass_yds": {"line": 255.5, "p_over": 0.5},
    }
    st = odds.stat_line(mk, "QB")
    lam = st["pass_td"]  # P(X >= 2) = 0.5 -> lambda ~1.68
    assert 1 - math.exp(-lam) * (1 + lam) == pytest.approx(0.5, abs=1e-3)
    assert st["rush_td"] == pytest.approx(-math.log(0.6), abs=1e-3)
    assert st["pass_yd"] == 255.5
    assert odds.stat_line({"player_anytime_td": {"p_yes": 0.4}}, "WR").keys() >= {"rec_td"}


def test_anytime_td_devigs_against_no_or_assumes_a_margin():
    yes_no = [{"name": "Yes", "description": "A", "price": 150},
              {"name": "No", "description": "A", "price": -200}]  # fmt: skip
    yes_only = [{"name": "Yes", "description": "B", "price": 150}]
    s = odds.summarize(_event([_book({"player_anytime_td": yes_no + yes_only})]))
    assert s["A"]["player_anytime_td"]["p_yes"] == pytest.approx(0.4 / (0.4 + 2 / 3))
    assert s["B"]["player_anytime_td"]["p_yes"] == pytest.approx(0.4 / 1.07)


def test_snapshot_props_maps_players_skips_started_games_and_stores_no_key(monkeypatch, tmp_path):
    con = db.connect(tmp_path / "t.duckdb")
    con.execute(
        "INSERT INTO player_status_snapshot (snapshot_id, platform, player_id, full_name, "
        "position, nfl_team) VALUES ('s', 'sleeper', '4046', 'Patrick Mahomes', 'QB', 'KC')"
    )
    now = datetime(2026, 11, 8, 17, 30, tzinfo=UTC)
    events = [
        {"id": "e1", "commence_time": "2026-11-08T18:00:00Z", **_event([])},
        {"id": "e0", "commence_time": "2026-11-08T17:00:00Z", **_event([])},  # already started
    ]
    calls = []

    class R:
        def __init__(self, body):
            self.body, self.headers = body, {"x-requests-remaining": "400"}

        def json(self):
            return self.body

    def fake_get(path, **params):
        calls.append(path)
        if path == "/events":
            return R(events)
        markets = {
            "player_pass_yds": ou("Patrick Mahomes", 270.5, -115, -105),
            "player_rush_yds": ou("Nobody Known", 10.5, -110, -110),
        }
        return R(_event([_book(markets)]))

    monkeypatch.setattr(odds, "_get", fake_get)
    monkeypatch.setenv("ODDS_API_KEY", "secret-key")
    assert odds.snapshot_props(con, "snap", 2026, 10, now=now) == 1
    assert calls == ["/events", "/events/e1/odds"]
    pid, st = con.execute(
        "SELECT source_player_id, stats FROM projection_snapshot WHERE source = 'props'"
    ).fetchall()[0]
    assert pid == "4046" and '"pass_yd":270.5' in st.replace(" ", "")
    urls = " ".join(u for (u,) in con.execute("SELECT url FROM raw_response").fetchall())
    assert "secret-key" not in urls


def test_missing_key_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setattr(odds, "KEY_FILE", tmp_path / "none")
    with pytest.raises(odds.NoKey):
        odds.api_key()
