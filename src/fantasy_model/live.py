"""Live chop odds during games: final score = points already scored + a draw for the share of
each starter's game still to play. Live scores and starters: Sleeper matchups (league-scored).
Game clocks: ESPN's public scoreboard. Nothing is written to the database (safe to poll).
"""

from __future__ import annotations

import numpy as np
import polars as pl
import requests

from fantasy_model import fit, player_sim, sleeper, value

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_TO_SLEEPER = {"WSH": "WAS"}
GAME_MIN, QUARTER_MIN = 60.0, 15.0

session = requests.Session()
session.headers["User-Agent"] = "fantasy-model/0.1 (personal use)"


def remaining_share(scoreboard: dict) -> dict[str, float]:
    """{team: share of its game left}: 1 before kickoff, 0 when final or on bye."""
    out = {}
    for ev in scoreboard.get("events", []):
        comp = ev["competitions"][0]
        st = comp["status"]
        state = st["type"]["state"]
        if state == "pre":
            share = 1.0
        elif state == "post" or st["type"].get("completed"):
            share = 0.0
        else:
            period = int(st.get("period") or 1)
            mins, _, secs = (st.get("displayClock") or "0:00").partition(":")
            clock = float(mins or 0) + float(secs or 0) / 60
            left = max(4 - period, 0) * QUARTER_MIN + (clock if period <= 4 else 0.0)
            share = min(max(left / GAME_MIN, 0.0), 1.0)
        for c in comp["competitors"]:
            abbr = c["team"]["abbreviation"]
            out[ESPN_TO_SLEEPER.get(abbr, abbr)] = share
    return out


def simulate_finals(
    current: np.ndarray, draws: np.ndarray, share: np.ndarray, lineups: list[list[int]]
) -> np.ndarray:
    """(n_sims, teams) final scores. current/share per player; draws (n_sims, players) full-game
    outcomes; the unplayed share of each game adds share x draw."""
    rest = draws * share[None, :]
    return np.stack([current[ix].sum() + rest[:, ix].sum(axis=1) for ix in lineups], axis=1)


def live_odds(con, ctx, n_sims: int = 20000, scoreboard: dict | None = None) -> dict:
    """Per alive team: points so far, projected final (mean, 10-90%), P(lowest this week)."""
    week = int(sleeper.session.get(f"{sleeper.BASE}/state/nfl", timeout=30).json()["week"])
    matchups = sleeper.session.get(
        f"{sleeper.BASE}/league/{ctx.league_id}/matchups/{week}", timeout=30
    ).json()
    sb = scoreboard or session.get(SCOREBOARD, timeout=30).json()
    left = remaining_share(sb)

    vals = fit.adjust_for_injury(
        value.player_values(con, ctx.platform, ctx.league_id, [week]), [week]
    ).rename({f"pts_w{week}": "mu"})
    meta = {r[0]: r[1:] for r in vals.select("player_id", "position", "nfl_team", "mu").iter_rows()}

    teams, lineups, players = [], [], []
    col: dict[str, int] = {}
    for m in matchups:
        t = str(m["roster_id"])
        if t not in ctx.alive:
            continue
        pts = m.get("players_points") or {}
        ix = []
        for pid in m.get("starters") or []:
            if not pid or pid == "0":
                continue
            if pid not in col:
                pos, team, mu = meta.get(pid, (None, None, 0.0))
                col[pid] = len(players)
                players.append(
                    {
                        "player_id": pid,
                        "position": pos,
                        "nfl_team": team,
                        "mu": mu or 0.0,
                        "current": float(pts.get(pid, 0.0)),
                        "share": left.get(team, 0.0) if team else 0.0,
                    }  # fmt: skip
                )
            ix.append(col[pid])
        teams.append(t)
        lineups.append(ix)
    df = pl.DataFrame(players)
    draws = player_sim.sample_players(
        df.select("position", "nfl_team", "mu"),
        player_sim.load_bank(con),
        n_sims,
        np.random.default_rng(0),
    )
    finals = simulate_finals(df["current"].to_numpy(), draws, df["share"].to_numpy(), lineups)
    p_low = np.bincount(finals.argmin(axis=1), minlength=len(teams)) / n_sims
    out = []
    for j, t in enumerate(teams):
        ix = lineups[j]
        out.append(
            {
                "team_id": t,
                "name": ctx.names.get(t, t),
                "is_me": t == ctx.me,
                "current": round(float(df["current"].to_numpy()[ix].sum()), 2),
                "final_mean": round(float(finals[:, j].mean()), 1),
                "final_p10": round(float(np.quantile(finals[:, j], 0.1)), 1),
                "final_p90": round(float(np.quantile(finals[:, j], 0.9)), 1),
                "share_left": round(float(df["share"].to_numpy()[ix].mean()), 2) if ix else 0.0,
                "p_chop": round(float(p_low[j]), 4),
            }
        )
    out.sort(key=lambda r: -r["p_chop"])
    games = {
        "pre": sum(1 for v in left.values() if v == 1.0) // 2,
        "live": sum(1 for v in left.values() if 0 < v < 1) // 2,
        "final": sum(1 for v in left.values() if v == 0.0) // 2,
    }
    low = finals.min(axis=1)  # the score that gets chopped, per simulated finish
    line = {
        k: round(float(np.quantile(low, q)), 1)
        for k, q in (("p10", 0.1), ("p50", 0.5), ("p90", 0.9))
    }
    return {"week": week, "games": games, "teams": out, "chop_line": line}
