"""Local web API for the UI (`ffm serve`). Read-only over the DuckDB file.

Speed: everything derived from a data snapshot is cached in memory, keyed by the newest
snapshot ids, so pages are instant after the first hit and refresh themselves after each
scheduled job. Slow work (bid sheet ~10s, model reports ~30s) runs in a background thread;
those endpoints answer {"status": "computing"} until ready, and the UI polls.
"""

from __future__ import annotations

import mimetypes
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import duckdb
import numpy as np
import polars as pl
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fantasy_model import config, fit, recommend

WEB_DIST = config.PROJECT_ROOT / "web" / "dist"
mimetypes.add_type("application/manifest+json", ".webmanifest")  # PWA manifest
PLATFORM = "sleeper"


# ------------------------------------------------------------------ data access
def connect(retries: int = 20, wait: float = 0.5) -> duckdb.DuckDBPyConnection:
    """Read-only connection; a scheduled job holding the write lock makes us wait, not fail."""
    for _ in range(retries):
        try:
            return duckdb.connect(str(config.DB_PATH), read_only=True)
        except duckdb.IOException:
            time.sleep(wait)
    raise HTTPException(503, "database busy (a scheduled job is writing); retry shortly")


def league_id() -> str:
    lgs = config.leagues(config.load_config(), PLATFORM)
    if not lgs:
        raise HTTPException(404, "no Sleeper league in config/leagues.toml")
    return lgs[0]["league_id"]


def data_version(con) -> str:
    """Changes whenever a job lands new rosters, projections or team state."""
    rows = con.execute(
        """SELECT (SELECT max(snapshot_id) FROM projection_snapshot),
                  (SELECT max(snapshot_id) FROM roster_snapshot),
                  (SELECT max(snapshot_id) FROM team_state_snapshot)"""
    ).fetchall()[0]
    return "|".join(str(r) for r in rows)


# ------------------------------------------------------------------ caching
class Cache:
    """Memory cache keyed by (name, data version, args). Slow keys compute in the background."""

    def __init__(self, workers: int = 2):
        self.done: dict[tuple, object] = {}
        self.running: dict[tuple, float] = {}
        self.errors: dict[tuple, str] = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=workers)

    def get(self, key: tuple, fn):
        """Compute synchronously (fast data)."""
        with self.lock:
            if key in self.done:
                return self.done[key]
        val = fn()
        with self.lock:
            self.done[key] = val
        return val

    def get_async(self, key: tuple, fn) -> dict:
        """Return {"status": "ready", "data"} or start/continue computing in the background."""
        with self.lock:
            if key in self.done:
                return {"status": "ready", "data": self.done[key]}
            if key in self.errors:
                return {"status": "error", "error": self.errors[key]}
            if key not in self.running:
                self.running[key] = time.time()
                self.pool.submit(self._run, key, fn)
            return {"status": "computing", "since": self.running[key]}

    def _run(self, key, fn):
        try:
            val = fn()
            with self.lock:
                self.done[key] = val
        except Exception:
            with self.lock:
                self.errors[key] = traceback.format_exc(limit=4)
        finally:
            with self.lock:
                self.running.pop(key, None)

    def clear_errors(self):
        with self.lock:
            self.errors.clear()


cache = Cache()


def _f(x, nd: int = 4):
    """JSON-safe float."""
    if x is None:
        return None
    x = float(x)
    return None if np.isnan(x) else round(x, nd)


# ------------------------------------------------------------------ computations
def _state(con, lid: str, assume_chop: str | None = None):
    ctx = recommend.load_context(con, PLATFORM, lid, assume_chop)
    return ctx, recommend.league_state(con, ctx, assume_chop)


def team_avatars(con, lid: str) -> dict[str, str]:
    """{team_id: image URL}: the team's custom avatar, else the manager's Sleeper avatar.
    From the newest stored league.users payload (no schema change, no extra request)."""
    import json

    raw = con.execute(
        """SELECT body FROM raw_response WHERE platform = ? AND endpoint = 'league.users'
           AND url LIKE ? ORDER BY fetched_at DESC LIMIT 1""",
        [PLATFORM, f"%/league/{lid}/users%"],
    ).fetchall()
    body = raw[0][0] if raw else []
    users = json.loads(body) if isinstance(body, str) else body
    by_user = {}
    for u in users or []:
        custom = (u.get("metadata") or {}).get("avatar")
        if custom:
            by_user[u["user_id"]] = custom
        elif u.get("avatar"):
            by_user[u["user_id"]] = f"https://sleepercdn.com/avatars/thumb/{u['avatar']}"
    owners = con.execute(
        "SELECT team_id, owner_id FROM team WHERE platform = ? AND league_id = ?", [PLATFORM, lid]
    ).fetchall()
    return {t: by_user[o] for t, o in owners if o in by_user}


def compute_timeline(con, lid: str) -> dict:
    """Next deadlines: the main waiver run (anchored on the last big run, +7 days until future),
    the next kickoff (lineups start locking) and the first Sunday kickoff. ISO UTC strings."""
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    now = datetime.now(UTC)
    last = con.execute(
        """SELECT processed_at FROM (SELECT processed_at, count(*) n FROM txn
           WHERE platform = ? AND league_id = ? AND type = 'waiver' GROUP BY 1)
           WHERE n >= 10 ORDER BY processed_at DESC LIMIT 1""",
        [PLATFORM, lid],
    ).fetchall()
    et = ZoneInfo("America/New_York")
    waiver = None
    if last:
        # Step a week at a time in Eastern wall-clock time (runs were ~3:08 am ET), so the
        # estimate stays right across a daylight-saving change.
        t = last[0][0].astimezone(et).replace(second=0, microsecond=0, tzinfo=None)
        while t.replace(tzinfo=et) <= now:
            t += timedelta(days=7)
        waiver = t.replace(tzinfo=et).astimezone(UTC)
    games = con.execute(
        """SELECT week, gameday, gametime, weekday FROM nfl_schedules
           WHERE game_type = 'REG' AND gameday >= ? ORDER BY gameday, gametime""",
        [(now - timedelta(days=1)).date().isoformat()],
    ).fetchall()
    kicks = []
    for wk, day, hhmm, weekday in games:
        if not hhmm:
            continue
        k = datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=et).astimezone(UTC)
        if k > now:
            kicks.append((wk, k, weekday))
    next_kick = kicks[0] if kicks else None
    sunday = next((k for k in kicks if k[2] == "Sunday" and k[1].astimezone(et).hour >= 12), None)
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "now": iso(now),
        "next_waiver": iso(waiver),
        "next_kickoff": iso(next_kick[1]) if next_kick else None,
        "next_kickoff_week": next_kick[0] if next_kick else None,
        "sunday_main": iso(sunday[1]) if sunday else None,
    }


def compute_league(con, lid: str) -> dict:
    ctx, st = _state(con, lid)
    base = st.base
    weeks = st.sim_weeks
    state = {
        t: (f, dead)
        for t, f, dead in con.execute(
            """SELECT team_id, faab_remaining, eliminated FROM team_state_snapshot
               WHERE snapshot_id = (SELECT max(snapshot_id) FROM team_state_snapshot
                                    WHERE platform = ? AND league_id = ?)""",
            [PLATFORM, lid],
        ).fetchall()
    }
    chopped = dict(
        con.execute(
            """SELECT team_id, min(week) FROM txn WHERE platform = ? AND league_id = ?
               AND type = 'chopped' AND team_id IS NOT NULL GROUP BY team_id""",
            [PLATFORM, lid],
        ).fetchall()
    )
    owners = dict(
        con.execute(
            "SELECT team_id, owner_name FROM team WHERE platform = ? AND league_id = ?",
            [PLATFORM, lid],
        ).fetchall()
    )
    avatars = team_avatars(con, lid)
    teams = []
    for t, name in ctx.names.items():
        alive = t in ctx.alive
        i = ctx.alive.index(t) if alive else None
        teams.append(
            {
                "team_id": t,
                "name": name,
                "owner": owners.get(t),
                "is_me": t == ctx.me,
                "avatar": avatars.get(t),
                "alive": alive,
                "faab": state.get(t, (None, None))[0],
                "chopped_week": chopped.get(t),
                "proj": {str(w): _f(st.proj[i, j], 1) for j, w in enumerate(weeks)}
                if alive
                else {},
                "p_chop_next": _f(base["p_chop"][i, 0]) if alive else None,
                "p_win": _f(base["p_win"][i]) if alive else 0.0,
                "exp_weeks": _f(base["exp_weeks"][i], 2) if alive else None,
                "survival": [_f(1 - x) for x in np.cumsum(base["p_chop"][i])] if alive else [],
            }
        )
    teams.sort(key=lambda r: (not r["alive"], -(r["p_win"] or 0), r["chopped_week"] or 0))
    return {
        "season": ctx.season,
        "week": ctx.week,
        "chop_pending": ctx.chop_pending,
        "weeks": weeks,
        "chops_left": len(ctx.alive) - 1,
        "me": ctx.me,
        "league_id": lid,
        "teams": teams,
    }


def compute_team(con, lid: str, team_id: str) -> dict:
    ctx, st = _state(con, lid)
    roster = st.vals.filter(pl.col("owner_team_id") == team_id)
    if roster.is_empty() and team_id not in ctx.names:
        raise HTTPException(404, f"unknown team {team_id}")
    starters = {}
    for w in st.sim_weeks:
        rows = list(roster.select("player_id", "position", f"pts_w{w}").iter_rows())
        starters[w] = set(fit.best_lineup_ids(rows, ctx.slots)[1])
    players = [
        {
            "player_id": r["player_id"],
            "name": r["full_name"],
            "pos": r["position"],
            "nfl_team": r["nfl_team"],
            "injury": r["injury_status"],
            "pts": {str(w): _f(r[f"pts_w{w}"], 1) for w in st.sim_weeks},
            "starts": [w for w in st.sim_weeks if r["player_id"] in starters[w]],
            "ros": _f(r["ros_pts"], 1),
        }
        for r in roster.sort("ros_pts", descending=True).iter_rows(named=True)
    ]
    return {
        "team_id": team_id,
        "name": ctx.names.get(team_id, team_id),
        "avatar": team_avatars(con, lid).get(team_id),
        "is_me": team_id == ctx.me,
        "weeks": st.sim_weeks,
        "players": players,
    }


def compute_player(con, lid: str, pid: str) -> dict:
    """One player: projections by source and week, next week's outcome range, league bids."""
    from fantasy_model import player_sim, value

    ctx = recommend.load_context(con, PLATFORM, lid)
    first = ctx.week if ctx.chop_pending else ctx.week + 1
    weeks = list(range(first, first + 4))
    vals = fit.adjust_for_injury(value.player_values(con, PLATFORM, lid, weeks), weeks)
    row = vals.filter(pl.col("player_id") == pid)
    if row.is_empty():
        raise HTTPException(404, f"no projection for player {pid}")
    r = row.row(0, named=True)
    scoring = value.league_scoring(con, PLATFORM, lid)
    by_source = {}
    for src in value.SOURCES:
        sp = value.source_points(con, scoring, src, weeks, ctx.season).filter(
            pl.col("player_id") == pid
        )
        if not sp.is_empty():
            by_source[src] = {str(w): _f(p, 1) for w, p in sp.select("week", "points").iter_rows()}

    # Outcome range for his next game (skip a bye): the empirical shape for his position and
    # projection level.
    game_wk = next((w for w in weeks if (r[f"pts_w{w}"] or 0) > 0), first)
    mu = r[f"pts_w{game_wk}"] or 0.0
    outcome = None
    if mu > 0:
        bank = player_sim.load_bank(con)
        ratios = bank.get((r["position"], player_sim.mu_bin(mu)))
        q = [0.1, 0.25, 0.5, 0.75, 0.9]
        if ratios is not None:
            vals_q = [float(mu * np.quantile(ratios, x)) for x in q]
        else:  # DEF: no bank yet; same rough spread the simulator uses
            from scipy.stats import norm

            vals_q = [max(float(mu * (1 + 0.5 * norm.ppf(x))), 0.0) for x in q]
        outcome = {
            "week": game_wk,
            "mean": _f(mu, 1),
            "p_dnp": _f(player_sim.dnp_prob(mu), 3),
            **{f"p{int(x * 100)}": _f(v, 1) for x, v in zip(q, vals_q, strict=True)},
        }

    claims = con.execute(
        """SELECT t.week, t.team_id, t.bid, t.status = 'complete', t.processed_at
           FROM txn t JOIN txn_player p USING (platform, league_id, transaction_id)
           WHERE t.platform = ? AND t.league_id = ? AND t.type = 'waiver'
             AND p.action = 'add' AND p.player_id = ?
           ORDER BY t.processed_at DESC, t.bid DESC""",
        [PLATFORM, lid, pid],
    ).fetchall()
    return {
        "player_id": pid,
        "name": r["full_name"],
        "pos": r["position"],
        "nfl_team": r["nfl_team"],
        "injury": r["injury_status"],
        "owner": ctx.names.get(r["owner_team_id"]) if r["owner_team_id"] else None,
        "owner_id": r["owner_team_id"],
        "is_mine": r["owner_team_id"] == ctx.me,
        "weeks": weeks,
        "consensus": {str(w): _f(r[f"pts_w{w}"], 1) for w in weeks},
        "by_source": by_source,
        "outcome": outcome,
        "bids": [
            {"week": wk, "team": ctx.names.get(t, t), "bid": b, "won": won}
            for wk, t, b, won, _ in claims
        ],
    }


def compute_players(con, lid: str) -> dict:
    ctx, st = _state(con, lid)
    v = st.vals.filter(pl.col("ros_pts") > 0.5)
    return {
        "weeks": st.sim_weeks,
        "players": [
            {
                "player_id": r["player_id"],
                "name": r["full_name"],
                "pos": r["position"],
                "nfl_team": r["nfl_team"],
                "injury": r["injury_status"],
                "owner": ctx.names.get(r["owner_team_id"]) if r["owner_team_id"] else None,
                "owner_id": r["owner_team_id"],
                "pts": [_f(r[f"pts_w{w}"], 1) for w in st.sim_weeks],
                "ros": _f(r["ros_pts"], 1),
            }
            for r in v.iter_rows(named=True)
        ],
    }


def compute_recommend(lid: str, assume_chop: str | None) -> dict:
    con = connect()
    try:
        ctx = recommend.load_context(con, PLATFORM, lid, assume_chop)
        out = recommend.recommend(con, ctx, assume_chop)
    finally:
        con.close()
    s = out["summary"]
    return {
        "summary": {k: (_f(v) if isinstance(v, float) else v) for k, v in s.items()},
        "sheet": [
            {k: (_f(v) if isinstance(v, float) else v) for k, v in r.items()}
            for r in out["sheet"].iter_rows(named=True)
        ],
        "plan": [
            {k: (_f(v) if isinstance(v, float) else v) for k, v in r.items()}
            for r in out["plan"].iter_rows(named=True)
        ],
    }


def compute_model_report(lid: str) -> dict:
    from fantasy_model import bids, evaluate

    con = connect()
    try:
        from fantasy_model import projection_model

        season = con.execute("SELECT max(season) FROM snapshot_run").fetchall()[0][0]
        last = projection_model.complete_through(con, season) - 1  # newest fully played week
        weeks = list(range(2, max(last, 2) + 1))
        proj = evaluate.model_backtest(con, PLATFORM, lid, season, weeks)
        sim = evaluate.sim_validation(con, PLATFORM, lid, season, list(range(1, last + 1)))
        price = bids.price_backtest(con, PLATFORM, lid, season, list(range(3, last + 1)))
    finally:
        con.close()
    return {
        "weeks": weeks,
        "projections": [
            {k: (_f(v) if isinstance(v, float) else v) for k, v in r.items()}
            for r in proj.iter_rows(named=True)
        ],
        "survival": {
            "summary": {k: _f(v) for k, v in sim["summary"].items()},
            "weeks": [
                {k: (_f(v) if isinstance(v, float) else v) for k, v in r.items()}
                for r in sim["weeks"].iter_rows(named=True)
            ],
        },
        "prices": {k: (_f(v) if isinstance(v, float) else v) for k, v in price.items()},
    }


# ------------------------------------------------------------------ app
def create_app() -> FastAPI:
    app = FastAPI(title="fantasy-model", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    def versioned(name: str, fn, *args):
        con = connect()
        try:
            lid = league_id()
            key = (name, data_version(con), *args)
            return cache.get(key, lambda: fn(con, lid, *args))
        finally:
            con.close()

    @app.get("/api/meta")
    def meta():
        con = connect()
        try:
            runs = con.execute(
                """SELECT snapshot_id, job, ok, started_at, finished_at FROM snapshot_run
                   ORDER BY started_at DESC LIMIT 15"""
            ).fetchall()
            return {
                "version": data_version(con),
                "runs": [
                    {
                        "id": r[0],
                        "job": r[1],
                        "ok": r[2],
                        "started": str(r[3]),
                        "finished": str(r[4]) if r[4] else None,
                    }  # fmt: skip
                    for r in runs
                ],
            }
        finally:
            con.close()

    @app.get("/api/league")
    def league():
        return versioned("league", compute_league)

    @app.get("/api/timeline")
    def timeline():
        con = connect()
        try:
            return compute_timeline(con, league_id())
        finally:
            con.close()

    @app.get("/api/teams/{team_id}")
    def team(team_id: str):
        return versioned("team", compute_team, team_id)

    @app.get("/api/players/{player_id}")
    def player(player_id: str):
        return versioned("player", compute_player, player_id)

    @app.get("/api/players")
    def players():
        return versioned("players", compute_players)

    live_cache: dict = {}

    @app.get("/api/live")
    def live_ep():
        """In-game chop odds. Not tied to data versions: cached 30 s to keep polling cheap."""
        hit = live_cache.get("v")
        if hit and time.time() - hit[0] < 30:
            return hit[1]
        from fantasy_model import live

        con = connect()
        try:
            lid = league_id()
            out = live.live_odds(con, recommend.load_context(con, PLATFORM, lid))
            avatars = team_avatars(con, lid)
            for t in out["teams"]:
                t["avatar"] = avatars.get(t["team_id"])
        finally:
            con.close()
        out["fetched_at"] = time.time()
        live_cache["v"] = (time.time(), out)
        return out

    @app.get("/api/lineup")
    def lineup_ep(week: int | None = None, team: str | None = None):
        def run(con, lid, week, team):
            from fantasy_model import lineup

            ctx = recommend.load_context(con, PLATFORM, lid)
            wk = week or (ctx.week if ctx.chop_pending else ctx.week + 1)
            out = lineup.optimize(con, ctx, wk, team)
            out["team_name"] = ctx.names.get(out["team"])
            return out

        return versioned("lineup", run, week, team)

    @app.get("/api/recommend")
    def rec(assume_chop: str | None = None):
        con = connect()
        try:
            key = ("recommend", data_version(con), assume_chop)
        finally:
            con.close()
        lid = league_id()
        return cache.get_async(key, lambda: compute_recommend(lid, assume_chop))

    @app.get("/api/model")
    def model():
        con = connect()
        try:
            key = ("model", data_version(con))
        finally:
            con.close()
        lid = league_id()
        return cache.get_async(key, lambda: compute_model_report(lid))

    @app.post("/api/retry")
    def retry():
        cache.clear_errors()
        return {"ok": True}

    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            f = WEB_DIST / path
            if path and f.is_file() and WEB_DIST in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(WEB_DIST / "index.html")  # client-side routes

    return app


def warm(lid: str | None = None) -> None:
    """Precompute in the background so first page loads are instant: league odds and players
    (~3 s each, now that rivals' pickups are simulated) and the default bid sheet (~15-20 s)."""
    try:
        con = connect()
        version = data_version(con)
        con.close()
        lid = lid or league_id()

        def fast(name, fn):
            def run():
                c = connect()
                try:
                    return fn(c, lid)
                finally:
                    c.close()

            return cache.get_async((name, version), run)

        fast("league", compute_league)
        fast("players", compute_players)
        cache.get_async(("recommend", version, None), lambda: compute_recommend(lid, None))
    except Exception:
        pass


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    warm()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
