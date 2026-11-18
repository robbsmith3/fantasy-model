"""Read-only Yahoo Fantasy client: OAuth refresh, allowlisted GET resources, throttle and backoff.

Nothing here writes Yahoo data anywhere: responses are returned to the caller and never cached,
and errors carry the endpoint name and status code but never the response body.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from urllib.parse import urlencode

import requests

from waivers.config import Credentials, League

API = "https://fantasysports.yahooapis.com/fantasy/v2"
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
HOSTS = {"fantasysports.yahooapis.com", "api.login.yahoo.com"}
RETRY_STATUS = {429, 999, 500, 502, 503, 504}  # 999 is Yahoo's rate-limit status
BACKOFF_BASE, BACKOFF_CAP = 1.0, 60.0
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

log = logging.getLogger(__name__)


class YahooError(Exception):
    """A failed request. Deliberately holds no response body."""

    def __init__(self, endpoint: str, status: int | None, why: str = ""):
        self.endpoint, self.status = endpoint, status
        super().__init__(f"Yahoo {endpoint} failed (status {status}){': ' + why if why else ''}")


class ScopeError(Exception):
    """A request for a league or team that isn't in config."""


def authorize_url(client_id: str, redirect_uri: str = "oob") -> str:
    query = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code"}
    return f"{AUTH_URL}?{urlencode(query)}"


def exchange_code(creds: Credentials, code: str, redirect_uri: str = "oob", session=None) -> str:
    """One-time consent: authorization code -> refresh token."""
    r = (session or requests.Session()).post(
        TOKEN_URL,
        auth=(creds.client_id, creds.client_secret),
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        timeout=30,
    )
    if r.status_code != 200:
        raise YahooError("oauth.code", r.status_code)
    return r.json()["refresh_token"]


class YahooClient:
    def __init__(
        self,
        creds: Credentials,
        leagues: list[League],
        requests_per_second: float = 1.0,
        max_retries: int = 5,
        session=None,
        on_new_refresh_token: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
    ):
        if not creds.refresh_token:
            raise YahooError(
                "oauth.refresh", None, "no refresh token: run `python -m waivers auth`"
            )
        self.creds = creds
        self.teams = {lg.league_key: lg.team_key for lg in leagues}  # the allowlist
        self.min_interval = 1.0 / requests_per_second
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.on_new_refresh_token = on_new_refresh_token
        self.clock, self.sleep, self.rand = clock, sleep, rand
        self._access: str | None = None
        self._last: float | None = None
        self.calls = 0

    # ---------------------------------------------------------------- allowlisted resources
    def league_settings(self, league_key: str) -> dict:
        self._check(league_key)
        return self._get(f"league/{league_key}/settings", "league.settings")

    def team(self, league_key: str) -> dict:
        """My team (FAAB balance)."""
        return self._get(f"team/{self._my_team(league_key)}", "team")

    def roster(self, league_key: str) -> dict:
        return self._get(f"team/{self._my_team(league_key)}/roster", "team.roster")

    def transactions(self, league_key: str, count: int = 50) -> dict:
        self._check(league_key)
        return self._get(
            f"league/{league_key}/transactions;types=add,drop;count={int(count)}",
            "league.transactions",
        )

    def available(self, league_key: str, position: str, count: int) -> dict:
        """Top `count` available players at one position, by Yahoo's actual rank. No stats."""
        self._check(league_key)
        if position not in POSITIONS:
            raise ScopeError(f"position {position!r} isn't one of {POSITIONS}")
        return self._get(
            f"league/{league_key}/players;status=A;position={position};sort=AR;count={int(count)}",
            f"league.players.{position}",
        )

    def _check(self, league_key: str) -> None:
        if league_key not in self.teams:
            raise ScopeError(f"league {league_key} isn't in config")

    def _my_team(self, league_key: str) -> str:
        self._check(league_key)
        return self.teams[league_key]

    # ---------------------------------------------------------------- transport
    def _throttle(self) -> None:
        if self._last is not None:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        delay = self.rand() * min(BACKOFF_CAP, BACKOFF_BASE * 2**attempt)  # full jitter
        if retry_after and retry_after.isdigit():
            delay = max(delay, float(retry_after))
        return delay

    def _refresh(self) -> None:
        self._throttle()
        r = self.session.post(
            TOKEN_URL,
            auth=(self.creds.client_id, self.creds.client_secret),
            data={"grant_type": "refresh_token", "refresh_token": self.creds.refresh_token,
                  "redirect_uri": "oob"},
            timeout=30,
        )  # fmt: skip
        if r.status_code != 200:
            raise YahooError("oauth.refresh", r.status_code, "run `python -m waivers auth` again")
        body = r.json()
        self._access = body["access_token"]
        new = body.get("refresh_token")
        if new and new != self.creds.refresh_token:
            self.creds = Credentials(self.creds.client_id, self.creds.client_secret, new)
            if self.on_new_refresh_token:
                self.on_new_refresh_token(new)

    def _get(self, resource: str, endpoint: str) -> dict:
        if self._access is None:
            self._refresh()
        refreshed = False
        for attempt in range(self.max_retries + 1):
            self._throttle()
            self.calls += 1
            try:
                r = self.session.get(
                    f"{API}/{resource}",
                    params={"format": "json"},
                    headers={"Authorization": f"Bearer {self._access}"},
                    timeout=30,
                )
            except requests.RequestException as e:
                status, retry_after, why = None, None, type(e).__name__
            else:
                status, retry_after, why = r.status_code, r.headers.get("Retry-After"), ""
                if status == 200:
                    log.info("%s ok", endpoint)
                    return r.json()["fantasy_content"]
                if status == 401 and not refreshed:  # access token expired mid-run
                    refreshed = True
                    self._refresh()
                    continue
                if status not in RETRY_STATUS:
                    raise YahooError(endpoint, status)
            if attempt == self.max_retries:
                raise YahooError(endpoint, status, why or "retries exhausted")
            delay = self._backoff(attempt, retry_after)
            log.warning("%s status %s, retrying in %.1fs", endpoint, status, delay)
            self.sleep(delay)
        raise YahooError(endpoint, None, "retries exhausted")
