"""Betting-market player props (The Odds API) as a projection source, source = "props".

Props are set by markets with money on the line, so they may disagree with the projection sites
usefully (Sleeper, ESPN and FantasyPros errors correlate 0.97). Not in the consensus yet: they
join only if a backtest on collected weeks shows a gain (`ffm eval-props`, once a few weeks exist).

Snapshotted in `pre_kickoff` for games starting in the next WINDOW hours: there's no free
history, so each week has to be captured before kickoff. Cost = markets x games (listing events
is free), ~96 credits a week against the free plan's 500 a month; the quota guard drops
low-priority markets rather than run out.

The key is read from $ODDS_API_KEY or ~/.config/fantasy-model/odds-api.key (chmod 600), never from
the repo. It's sent as a query parameter, so URLs are stored without it.

Stored stat lines use Sleeper stat keys so league scoring applies: median line across books for
yards/receptions; Poisson means for passing TDs and anytime TDs (from de-vigged probabilities).
"""

from __future__ import annotations

import math
import os
import re
import statistics
from datetime import UTC, datetime, timedelta

import requests

from fantasy_model import config
from fantasy_model.db import land_raw, upsert

PLATFORM = "props"
BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"
KEY_FILE = config.SECRETS_DIR / "odds-api.key"
WINDOW = timedelta(hours=36)  # Thu run: Thursday's game; Sun run: Sunday + Monday
RESERVE = 20  # credits left untouched
# Priority order: the quota guard drops from the end.
MARKETS = (
    "player_anytime_td",
    "player_reception_yds",
    "player_rush_yds",
    "player_pass_yds",
    "player_receptions",
    "player_pass_tds",
)
LINE_STAT = {
    "player_pass_yds": "pass_yd",
    "player_rush_yds": "rush_yd",
    "player_reception_yds": "rec_yd",
    "player_receptions": "rec",
}
ANYTIME_ONE_SIDED_MARGIN = 1.07  # typical overround when a book posts only "Yes"
TEAMS = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}  # fmt: skip


class NoKey(RuntimeError):
    pass


def api_key() -> str:
    key = os.environ.get("ODDS_API_KEY")
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    if not key:
        raise NoKey(f"no Odds API key: set ODDS_API_KEY or write it to {KEY_FILE} (chmod 600)")
    return key


def _get(path: str, **params) -> requests.Response:
    r = requests.get(f"{BASE}{path}", params={"apiKey": api_key(), **params}, timeout=30)
    r.raise_for_status()
    return r


# ------------------------------------------------------------------ parsing


def norm(name: str) -> str:
    """Name key: lowercase letters only, suffixes dropped ("D.J. Moore Jr." -> "djmoore")."""
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\.?$", "", name.lower().strip())
    return re.sub(r"[^a-z]", "", name)


def _dec(price: float) -> float:
    """American or decimal odds -> decimal."""
    if price >= 100 or price <= -100:
        return 1 + (price / 100 if price > 0 else 100 / -price)
    return price


def _poisson_mean(line: float, p_over: float) -> float:
    """lambda with P(X > line) = p_over for a Poisson count (line like 0.5, 1.5, 2.5)."""
    k = math.floor(line)
    lo, hi = 1e-4, 10.0
    for _ in range(60):
        lam = (lo + hi) / 2
        cdf = sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(k + 1))
        lo, hi = (lam, hi) if 1 - cdf < p_over else (lo, lam)
    return (lo + hi) / 2


def summarize(event: dict) -> dict[str, dict]:
    """{player name: {market: {"line", "p_over"} | {"p_yes"}}}, medians across bookmakers."""
    quotes: dict[tuple[str, str], dict] = {}
    for book in event.get("bookmakers", []):
        for m in book.get("markets", []):
            by_player: dict[str, dict] = {}
            for o in m.get("outcomes", []):
                if o.get("description"):
                    by_player.setdefault(o["description"], {})[o["name"]] = o
            for player, sides in by_player.items():
                q = quotes.setdefault((player, m["key"]), {"line": [], "p": []})
                if m["key"] == "player_anytime_td":
                    yes, no = sides.get("Yes"), sides.get("No")
                    if not yes:
                        continue
                    py = 1 / _dec(yes["price"])
                    py = py / (py + 1 / _dec(no["price"])) if no else py / ANYTIME_ONE_SIDED_MARGIN
                    q["p"].append(py)
                else:
                    over, under = sides.get("Over"), sides.get("Under")
                    if not over or over.get("point") is None:
                        continue
                    po = 1 / _dec(over["price"])
                    pu = 1 / _dec(under["price"]) if under else None
                    q["line"].append(over["point"])
                    q["p"].append(po / (po + pu) if pu else 0.5)
    out: dict[str, dict] = {}
    for (player, market), q in quotes.items():
        if not q["p"]:
            continue
        entry = {"p_yes" if market == "player_anytime_td" else "p_over": statistics.median(q["p"])}
        if q["line"]:
            entry["line"] = statistics.median(q["line"])
        out.setdefault(player, {})[market] = entry
    return out


def stat_line(markets: dict, position: str) -> dict:
    """Sleeper-keyed stats from one player's summarized markets (only what props cover)."""
    st: dict[str, float] = {}
    for m, key in LINE_STAT.items():
        if m in markets and "line" in markets[m]:
            st[key] = markets[m]["line"]
    if "line" in markets.get("player_pass_tds", {}):
        q = markets["player_pass_tds"]
        st["pass_td"] = round(_poisson_mean(q["line"], q["p_over"]), 3)
    if "player_anytime_td" in markets:
        p = min(markets["player_anytime_td"]["p_yes"], 0.95)
        st["rec_td" if position in ("WR", "TE") else "rush_td"] = round(-math.log(1 - p), 3)
    st["_markets"] = sorted(markets)  # which stats the line covers; the rest come from elsewhere
    return st


# ------------------------------------------------------------------ snapshot


def players_by_name(con) -> dict[tuple[str, str], tuple[str, str]]:
    """{(name key, team): (sleeper id, position)} from the latest player status snapshot."""
    rows = con.execute(
        """SELECT DISTINCT ON (player_id) player_id, full_name, position, nfl_team
           FROM player_status_snapshot WHERE platform = 'sleeper'
             AND position IN ('QB', 'RB', 'WR', 'TE')
           ORDER BY player_id, snapshot_id DESC"""
    ).fetchall()
    return {(norm(n), t): (pid, pos) for pid, n, pos, t in rows if n and t}


def snapshot_props(con, snapshot_id: str, season: int, week: int, now=None) -> int:
    now = now or datetime.now(UTC)
    events = _get("/events").json()
    land_raw(con, snapshot_id, PLATFORM, "events", f"{BASE}/events", 200, events)
    upcoming = [
        e for e in events
        if now < datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) <= now + WINDOW
    ]  # fmt: skip
    if not upcoming:
        return 0
    names = players_by_name(con)
    rows, unmatched, remaining = [], [], None
    for e in upcoming:
        markets = list(MARKETS)
        if remaining is not None:  # quota guard: keep the highest-priority markets that fit
            markets = markets[: max(0, remaining - RESERVE)]
            if not markets:
                raise RuntimeError(f"Odds API quota nearly used up ({remaining} credits left)")
        path = f"/events/{e['id']}/odds"
        params = {"regions": "us", "markets": ",".join(markets), "oddsFormat": "american"}
        r = _get(path, **params)
        remaining = int(r.headers.get("x-requests-remaining", 10**6))
        body = r.json()
        land_raw(con, snapshot_id, PLATFORM, "event_odds", f"{BASE}{path}?{params}", 200, body)
        teams = {TEAMS.get(e["home_team"]), TEAMS.get(e["away_team"])}
        for player, mk in summarize(body).items():
            team = next((t for t in teams if (norm(player), t) in names), None)
            if team is None:
                unmatched.append(player)
                continue
            pid, pos = names[(norm(player), team)]
            rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "source": PLATFORM,
                    "season": season,
                    "week": week,
                    "source_player_id": pid,
                    "position": pos,
                    "nfl_team": team,
                    "stats": stat_line(mk, pos),
                }
            )
    n = upsert(con, "projection_snapshot", rows)
    print(f"props: {n} players from {len(upcoming)} games, {len(unmatched)} unmatched, "
          f"{remaining} credits left")  # fmt: skip
    if unmatched:
        print("  unmatched:", ", ".join(sorted(unmatched)[:20]))
    return n
