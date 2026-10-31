"""Yahoo adapter: OAuth2 + fetch -> land raw -> parse into common schema.

Yahoo's JSON is lists of single-key dicts and {"0": {...}, "count": N} containers;
`_merge` / `_items` flatten them. Every response is landed raw first, so if a parser
guess is wrong you re-parse from raw_response instead of losing the snapshot.

VERIFY ON FIRST REAL RUN: `ffm yahoo-probe <league_key>` confirms faab_bid is present.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

import requests

from fantasy_model import config
from fantasy_model.db import land_raw, now_utc, upsert

API = "https://fantasysports.yahooapis.com/fantasy/v2"
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
PLATFORM = "yahoo"
BENCH = {"BN", "IR", "IR+", "NA"}

session = requests.Session()


# ------------------------------------------------------------------ OAuth
def token_path() -> Path:
    return config.SECRETS_DIR / "yahoo_token.json"


def _save_token(tok: dict) -> None:
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.write_text(json.dumps(tok, indent=2))
    p.chmod(0o600)


def authorize(redirect_uri: str = "oob") -> None:
    """One-time interactive login. Run via `op run` so the client secret comes from 1Password."""
    cid, secret = os.environ.get("YAHOO_CLIENT_ID"), os.environ.get("YAHOO_CLIENT_SECRET")
    if not cid or not secret:
        raise SystemExit("Set YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET (use `op run --env-file`).")
    q = urllib.parse.urlencode(
        {"client_id": cid, "redirect_uri": redirect_uri, "response_type": "code"}
    )
    print(f"Open this URL, approve, then paste the code:\n\n  {AUTH_URL}?{q}\n")
    print("(If redirect_uri is https://localhost..., copy `code=` from the address bar.)")
    code = input("code> ").strip()
    r = session.post(
        TOKEN_URL,
        auth=(cid, secret),
        timeout=30,
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
    )
    r.raise_for_status()
    tok = r.json()
    _save_token(
        {
            "client_id": cid,
            "client_secret": secret,
            "redirect_uri": redirect_uri,
            "refresh_token": tok["refresh_token"],
            "access_token": tok["access_token"],
            "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60,
        }
    )
    print(f"Saved {token_path()} (chmod 600). Unattended runs refresh from here.")


def _access_token(force_refresh: bool = False) -> str:
    p = token_path()
    if not p.exists():
        raise SystemExit("No Yahoo token. Run `ffm yahoo-auth` once.")
    tok = json.loads(p.read_text())
    if force_refresh or time.time() >= tok.get("expires_at", 0):
        r = session.post(
            TOKEN_URL,
            auth=(tok["client_id"], tok["client_secret"]),
            timeout=30,
            data={
                "grant_type": "refresh_token",
                "refresh_token": tok["refresh_token"],
                "redirect_uri": tok.get("redirect_uri", "oob"),
            },
        )
        r.raise_for_status()
        new = r.json()
        tok["access_token"] = new["access_token"]
        tok["refresh_token"] = new.get("refresh_token", tok["refresh_token"])
        tok["expires_at"] = time.time() + int(new.get("expires_in", 3600)) - 60
        _save_token(tok)
    return tok["access_token"]


def _get(con, snapshot_id: str, endpoint: str, path: str):
    url = f"{API}/{path}"
    for attempt in range(3):
        try:
            r = session.get(
                url,
                params={"format": "json"},
                timeout=30,
                headers={"Authorization": f"Bearer {_access_token(attempt > 0)}"},
            )
        except (requests.ConnectionError, requests.Timeout):
            time.sleep(2**attempt)
            continue
        if r.status_code == 401 and attempt == 0:
            continue  # expired early; refresh and retry
        body = r.json() if r.ok else None
        land_raw(con, snapshot_id, PLATFORM, endpoint, r.url, r.status_code, body)
        r.raise_for_status()
        return body["fantasy_content"]
    raise RuntimeError(f"yahoo GET failed: {path}")


# ------------------------------------------------------------------ JSON flatteners
def _merge(x) -> dict:
    """[{a:1},{b:2},[{c:3}],[]] -> {a:1,b:2,c:3}"""
    out: dict = {}
    if isinstance(x, dict):
        return x
    for el in x or []:
        if isinstance(el, dict):
            out.update(el)
        elif isinstance(el, list):
            out.update(_merge(el))
    return out


def _items(container, key: str):
    """{"0": {key: v0}, "1": {key: v1}, "count": 2} -> v0, v1"""
    if not isinstance(container, dict):
        return
    for k in sorted((k for k in container if k.isdigit()), key=int):
        yield container[k][key]


def _player(p) -> tuple[dict, dict]:
    """player = [[meta...], {extra}, ...] -> (meta, extras)"""
    return _merge(p[0]), _merge(p[1:])


# ------------------------------------------------------------------ discovery
def my_leagues(con, snapshot_id: str) -> list[dict]:
    fc = _get(con, snapshot_id, "user.leagues", "users;use_login=1/games;game_codes=nfl/leagues")
    out = []
    for user in _items(fc["users"], "user"):
        for game in _items(_merge(user[1:])["games"], "game"):
            gmeta, gextra = _merge(game[0] if isinstance(game, list) else game), _merge(game[1:])
            for lg in _items(gextra.get("leagues", {}), "league"):
                m = _merge(lg)
                out.append(
                    {
                        "season": gmeta.get("season"),
                        "league_key": m.get("league_key"),
                        "name": m.get("name"),
                        "num_teams": m.get("num_teams"),
                    }
                )
    return out


# ------------------------------------------------------------------ league structure
def sync_league(con, snapshot_id: str, league_key: str) -> None:
    fc = _get(con, snapshot_id, "league.settings", f"league/{league_key}/settings")
    meta, extra = _merge(fc["league"][0]), _merge(fc["league"][1:])
    settings = _merge(extra.get("settings", {}))
    upsert(
        con,
        "league",
        [
            {
                "platform": PLATFORM,
                "league_id": league_key,
                "season": int(meta["season"]),
                "name": meta.get("name"),
                "total_rosters": _int(meta.get("num_teams")),
                "faab_budget": _int(settings.get("faab_budget")),
                "scoring": settings.get("stat_modifiers"),
                "roster_slots": settings.get("roster_positions"),
                "settings": settings,
                "previous_league_id": meta.get("renew") or None,
                "updated_at": now_utc(),
            }
        ],
    )

    fc = _get(con, snapshot_id, "league.teams.roster", f"league/{league_key}/teams/roster")
    teams = _merge(fc["league"][1:])["teams"]
    team_rows, roster_rows, state_rows = [], [], []
    for t in _items(teams, "team"):
        tm, textra = _merge(t[0]), _merge(t[1:])
        tkey = tm["team_key"]
        mgr = _merge(next(iter(_items_list(tm.get("managers"), "manager")), {}))
        team_rows.append(
            {
                "platform": PLATFORM,
                "league_id": league_key,
                "team_id": tkey,
                "owner_id": mgr.get("guid"),
                "owner_name": mgr.get("nickname"),
                "team_name": tm.get("name"),
                "is_me": str(tm.get("is_owned_by_current_login", "0")) == "1",
            }
        )
        roster = textra.get("roster", {})
        players_container = _merge(roster.get("0", {})).get("players", {})
        n = 0
        for p in _items(players_container, "player"):
            pm, pextra = _player(p)
            sel = _merge(pextra.get("selected_position", []))
            roster_rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "platform": PLATFORM,
                    "league_id": league_key,
                    "team_id": tkey,
                    "player_id": str(pm["player_id"]),
                    "is_starter": sel.get("position") not in BENCH,
                }
            )
            n += 1
        state_rows.append(
            {
                "snapshot_id": snapshot_id,
                "platform": PLATFORM,
                "league_id": league_key,
                "team_id": tkey,
                "faab_remaining": _int(tm.get("faab_balance")),
                "points_for": None,
                "n_players": n,
                "eliminated": n == 0,
            }
        )
    upsert(con, "team", team_rows)
    upsert(con, "roster_snapshot", roster_rows)
    upsert(con, "team_state_snapshot", state_rows)


def _items_list(x, key):
    """managers can be a list [{manager:{}}] or a numbered container."""
    if isinstance(x, list):
        return [el[key] for el in x if isinstance(el, dict) and key in el]
    return list(_items(x, key)) if x else []


def sync_week_scores(con, snapshot_id: str, league_key: str, season: int, week: int) -> int:
    fc = _get(
        con,
        snapshot_id,
        "league.teams.stats",
        f"league/{league_key}/teams/stats;type=week;week={week}",
    )
    rows = []
    for t in _items(_merge(fc["league"][1:])["teams"], "team"):
        tm, textra = _merge(t[0]), _merge(t[1:])
        pts = textra.get("team_points") or {}
        rows.append(
            {
                "platform": PLATFORM,
                "league_id": league_key,
                "season": season,
                "week": week,
                "team_id": tm["team_key"],
                "points": _float(pts.get("total")),
                "starters": None,
                "player_points": None,
            }
        )
    return upsert(con, "team_week", rows)


def sync_transactions(
    con, snapshot_id: str, league_key: str, season: int, page: int = 25, max_pages: int = 40
) -> int:
    total = 0
    for i in range(max_pages):
        fc = _get(
            con,
            snapshot_id,
            "league.transactions",
            f"league/{league_key}/transactions;start={i * page};count={page}",
        )
        txns = list(_items(_merge(fc["league"][1:]).get("transactions", {}), "transaction"))
        total += _parse_transactions(con, league_key, season, txns)
        if len(txns) < page:
            break
    return total


def _parse_transactions(con, league_key: str, season: int, txns: list) -> int:
    t_rows, p_rows = [], []
    for t in txns:
        tm, textra = _merge(t[0]), _merge(t[1:])
        tid = tm["transaction_key"]
        team = None
        for p in _items(textra.get("players", {}), "player"):
            pm, pextra = _player(p)
            td = pextra.get("transaction_data")
            td = td[0] if isinstance(td, list) else (td or {})
            action = td.get("type")
            tteam = td.get("destination_team_key") if action == "add" else td.get("source_team_key")
            team = team or (tteam if action == "add" else None)
            p_rows.append(
                {
                    "platform": PLATFORM,
                    "league_id": league_key,
                    "transaction_id": tid,
                    "player_id": str(pm["player_id"]),
                    "action": action,
                    "team_id": tteam,
                    "source_type": td.get("source_type"),
                }
            )
            if action == "drop" and team is None:
                team = tteam
        ts = _int(tm.get("timestamp"))
        t_rows.append(
            {
                "platform": PLATFORM,
                "league_id": league_key,
                "transaction_id": tid,
                "season": season,
                "week": None,
                "type": tm.get("type"),
                "status": tm.get("status"),
                "team_id": team,
                "bid": _int(tm.get("faab_bid")),
                "created_at": None,
                "processed_at": datetime.fromtimestamp(ts, tz=UTC) if ts else None,
            }
        )
    upsert(con, "txn", t_rows)
    upsert(con, "txn_player", p_rows)
    return len(t_rows)


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
