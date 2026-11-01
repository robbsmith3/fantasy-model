"""How far each team's actual starters fall short of its best projected lineup, per week.

Tests the season simulation's assumption that rivals start their best lineups (DEVLOG 2026-11-01):
is the shortfall large, and is it a persistent trait of some teams?
Run: uv run python scripts/experiments/lineup_gap.py [SEASON]
"""

import json
import sys

import duckdb
import numpy as np
import polars as pl

from fantasy_model import bids, config, fit, value

season = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
con = duckdb.connect(str(config.DB_PATH), read_only=True)
lid = con.execute("SELECT league_id FROM league WHERE season = ?", [season]).fetchall()[0][0]
slots = bids.league_slots(con, "sleeper", lid)
scoring = value.league_scoring(con, "sleeper", lid)
n_starters = len([s for s in slots if s in fit.SLOT_ELIGIBLE])

rows = []
weeks = [w for (w,) in con.execute(
    "SELECT DISTINCT week FROM team_week WHERE league_id = ? AND season = ? AND points > 0",
    [lid, season],
).fetchall()]  # fmt: skip
for w in sorted(weeks)[:-1]:  # the latest week may still be in progress
    tw = con.execute(
        "SELECT team_id, points, starters, player_points FROM team_week "
        "WHERE league_id = ? AND season = ? AND week = ? AND points > 0",
        [lid, season, w],
    ).fetchall()
    src = [
        value.source_points(con, scoring, s, [w], season).select(
            "player_id", "position", pl.col("points").alias(s)
        )
        for s in ("sleeper", "espn")
    ]
    proj = src[0].join(src[1].drop("position"), on="player_id", how="full", coalesce=True)
    proj = proj.with_columns(pl.mean_horizontal("sleeper", "espn").alias("mu"))
    mu = {
        p: (pos, m or 0.0) for p, pos, m in proj.select("player_id", "position", "mu").iter_rows()
    }
    for t, pts, st, pp in tw:
        st = [p for p in (json.loads(st) if isinstance(st, str) else st) if p and p != "0"]
        roster = json.loads(pp) if isinstance(pp, str) else pp
        best = fit.best_lineup([(p, *mu[p]) for p in roster if p in mu], slots)
        started = sum(mu.get(p, (None, 0.0))[1] for p in st)
        dead = sum(1 for p in st if not mu.get(p, (None, 0.0))[1]) + n_starters - len(st)
        rows.append({"week": w, "team": t, "gap": best - started, "dead": dead, "actual": pts})

df = pl.DataFrame(rows)
print(f"team-weeks {df.height}: gap mean {df['gap'].mean():.2f}, median {df['gap'].median():.2f}, "
      f"p90 {df['gap'].quantile(0.9):.1f}; with a dead slot: {(df['dead'] > 0).sum()}")  # fmt: skip
by_team = df.group_by("team").agg(pl.col("gap").mean().alias("g"), pl.col("gap").var().alias("v"),
                                  pl.len().alias("n"))  # fmt: skip
mid = sorted(weeks)[len(weeks) // 2]
halves = (
    df.filter(pl.col("week") < mid).group_by("team").agg(pl.col("gap").mean().alias("a"))
    .join(df.filter(pl.col("week") >= mid).group_by("team").agg(pl.col("gap").mean().alias("b")),
          on="team")
)  # fmt: skip
r = np.corrcoef(halves["a"], halves["b"])[0, 1]
print(f"persistence corr(first half, second half) = {r:.2f} over {halves.height} teams")
print(f"between-team variance {by_team['g'].var():.2f} vs noise alone "
      f"{float((by_team['v'] / by_team['n']).mean()):.2f}")  # fmt: skip
