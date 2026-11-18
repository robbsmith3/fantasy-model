import pytest
from fixtures import LK, MARKER, FakeSession, Resp

from waivers.config import Credentials, League
from waivers.yahoo_client import ScopeError, YahooClient, YahooError

CREDS = Credentials("cid", "csecret", "refresh-0")
LEAGUES = [League("x", "111", "3")]


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def client(session, clock=None, **kw):
    clock = clock or Clock()
    return YahooClient(CREDS, LEAGUES, session=session, clock=clock.now, sleep=clock.sleep,
                       rand=lambda: 1.0, **kw)  # fmt: skip


def test_only_configured_leagues_and_my_team():
    c = client(FakeSession(0))
    c.league_settings(LK)
    with pytest.raises(ScopeError):
        c.league_settings("nfl.l.999")
    with pytest.raises(ScopeError):
        c.available(LK, "OL", 5)
    assert c.session.calls[-1][1].endswith(f"team/{LK}.t.3") is False  # no stray call happened
    c.team(LK)
    assert c.session.calls[-1][1].endswith(f"team/{LK}.t.3")


def test_every_api_call_is_a_get_and_throttled():
    clock = Clock()
    s = FakeSession(0)
    c = client(s, clock, requests_per_second=2.0)
    for _ in range(3):
        c.league_settings(LK)
    assert all(m == "GET" for m, u in s.calls if "fantasysports" in u)
    assert clock.slept == [0.5, 0.5, 0.5]  # refresh, then 3 GETs, each 0.5s apart


def test_backoff_on_999_and_429_then_success():
    clock = Clock()
    script = [Resp(999), Resp(429, headers={"Retry-After": "7"}), None]
    s = FakeSession(0)
    s.script = script[:2]
    c = client(s, clock, requests_per_second=1000.0)
    c.league_settings(LK)
    backoffs = [x for x in clock.slept if x >= 0.9]
    assert backoffs == [1.0, 7.0]  # 1s base with jitter at its max, then Retry-After wins


def test_retries_are_capped_and_errors_carry_no_body():
    s = FakeSession(0, script=[Resp(999, {"error": MARKER})] * 10)
    c = client(s, max_retries=3)
    with pytest.raises(YahooError) as e:
        c.league_settings(LK)
    gets = [u for m, u in s.calls if m == "GET"]
    assert len(gets) == 4 and MARKER not in str(e.value) and "999" in str(e.value)


def test_non_retryable_status_fails_fast_without_body():
    s = FakeSession(0, script=[Resp(403, {"x": MARKER})])
    with pytest.raises(YahooError) as e:
        client(s).league_settings(LK)
    assert e.value.status == 403 and MARKER not in str(e.value)


def test_expired_token_refreshes_once_and_new_refresh_token_is_handed_back():
    saved = []
    s = FakeSession(0, script=[Resp(401)])
    c = client(s, on_new_refresh_token=saved.append)
    c.league_settings(LK)
    assert [m for m, _ in s.calls].count("POST") == 2 and saved == ["refresh-1"]
