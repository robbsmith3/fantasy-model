import logging
import os
import time
from urllib.parse import urlsplit

import pytest
import requests
from fixtures import MARKER, FakeSession, Resp, public

from waivers import FOOTER
from waivers.__main__ import main
from waivers.config import Credentials, save_refresh_token
from waivers.yahoo_client import HOSTS, YahooClient

CONFIG = """
[[league]]
name = "fake"
league_id = "111"
team_id = "3"
[settings]
requests_per_second = 1000
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(CONFIG)
    monkeypatch.setenv("WAIVERS_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def no_network(*a, **k):
        raise AssertionError("real network call")

    monkeypatch.setattr(requests.Session, "request", no_network)
    return tmp_path


def _run(args, session):
    def factory(settings):
        return YahooClient(Credentials("c", "s", "r"), settings.leagues, 1000.0, 2, session=session,
                           sleep=lambda s: None)  # fmt: skip

    return main(args, client_factory=factory, data_loader=public)


def _files(root):
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_run_prints_top_adds_with_footer_and_writes_nothing(home, capsys):
    before = _files(home)
    session = FakeSession(int(time.time()))
    assert _run(["run"], session) == 0
    out = capsys.readouterr().out
    assert "1. Juliet Rising" in out and "bid $" in out and "drop " in out
    assert out.rstrip().endswith(FOOTER)
    assert _files(home) == before  # no file written without --save
    assert {urlsplit(u).hostname for _, u in session.calls} <= HOSTS
    assert {m for m, u in session.calls if "fantasysports" in u} == {"GET"}


def test_fetches_only_settings_my_team_roster_transactions_and_pools(home):
    session = FakeSession(int(time.time()))
    _run(["run"], session)
    paths = sorted(urlsplit(u).path.split("/v2/")[1] for m, u in session.calls if m == "GET")
    kinds = {p.split(";")[0].rsplit("/", 1)[-1] if "/" in p else p for p in paths}
    assert kinds <= {"settings", "nfl.l.111.t.3", "roster", "transactions", "players"}
    assert not any("stats" in p for p in paths)
    assert sum("players;" in p for p in paths) == 4  # QB, RB, WR, TE only (no K/DEF slots)


def test_save_writes_one_file_and_the_next_run_deletes_it_first(home, capsys):
    session = FakeSession(int(time.time()))
    _run(["run", "--save"], session)
    saved = _files(home) - {"config.toml"}
    assert len(saved) == 1 and next(iter(saved)).startswith("recs/")
    assert oct(os.stat(home / next(iter(saved))).st_mode)[-3:] == "600"
    first = next(iter(saved))
    _run(["run"], FakeSession(int(time.time()), script=[Resp(999)] * 50))  # fails, after cleanup
    assert first not in _files(home)


def test_old_files_go_and_purge_empties_recs(home, capsys):
    recs = home / "recs"
    recs.mkdir()
    old = recs / "old.txt"
    old.write_text("x")
    os.utime(old, (time.time() - 2 * 86400,) * 2)
    (recs / "new.txt").write_text("y")
    assert main(["purge"]) == 0
    assert list(recs.iterdir()) == []


def test_a_failing_league_shows_status_but_never_the_body(home, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    session = FakeSession(int(time.time()), script=[Resp(500, {"e": MARKER})] * 10)
    _run(["-v", "run"], session)
    out = capsys.readouterr()
    assert "couldn't load" in out.out and "500" in out.out
    assert MARKER not in out.out + out.err + caplog.text
    assert out.out.rstrip().endswith(FOOTER)


def test_unknown_league_is_a_config_error(home, capsys):
    assert _run(["run", "--league", "999"], FakeSession(0)) == 1
    assert "no configured league" in capsys.readouterr().err


def test_refresh_token_is_saved_owner_only(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("YAHOO_CLIENT_ID=c\nYAHOO_REFRESH_TOKEN=old\n")
    monkeypatch.delenv("YAHOO_REFRESH_TOKEN", raising=False)
    save_refresh_token(env, "new")
    text = env.read_text()
    assert "YAHOO_REFRESH_TOKEN=new" in text and "old" not in text and "YAHOO_CLIENT_ID=c" in text
    assert oct(env.stat().st_mode)[-3:] == "600"


def test_a_parsing_error_reports_type_and_line_but_not_values(home, capsys):
    session = FakeSession(int(time.time()))
    bad = {"fantasy_content": {"league": [{"name": MARKER}, {"settings": [{"roster_positions":
           [{"roster_position": {"position": "QB", "count": MARKER}}]}]}]}}  # fmt: skip
    session.script = [Resp(200, bad)]
    _run(["run"], session)
    out = capsys.readouterr()
    assert "couldn't process: ValueError at yahoo_parse.py" in out.out
    assert MARKER not in out.out + out.err
