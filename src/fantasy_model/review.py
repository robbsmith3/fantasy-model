"""Weekly report card: grade past predictions against what happened, then recalibrate.

For every main waiver run, refit the bid model on earlier runs only and check: was the actual
winning price inside the predicted 80% range, how far off was the median, did we name the
winner. Chop predictions are graded with evaluate.sim_validation. Two corrections are refit from
the results and saved to data/calibration.json, which production code reads:

- spread_k, level_s: multipliers on the bid model's residual spread and bid level, chosen to
  minimize CRPS (a proper score for the whole predicted range). Not tuned to hit 80% coverage:
  on 2026 legs 3-7 that needed ~2x spread and made CRPS 92.8 -> 149; the few big misses (Walker
  $569 vs $310 predicted) are better left as surprises than paid for in every auction.
- pay_scale: what the top expected bidder actually pays vs the model's bid (dynamics).

All clamped to [1, 2]; with ~10 contested auctions a week, wild values are noise.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import polars as pl

from fantasy_model import bids, config

CALIBRATION_PATH = config.DATA_DIR / "calibration.json"
MIN_WIN = 20  # a "contested" auction: winning bid at least this
SPREAD_GRID = (1.0, 1.25, 1.5, 1.75, 2.0)
LEVEL_GRID = (1.0, 1.15, 1.3, 1.5)
CLAMP = (1.0, 2.0)
GRID_SIMS = 5000  # per auction while searching; final scores use the default 20k


def load_calibration() -> dict:
    """Saved corrections, or {} (callers fall back to their built-in defaults)."""
    try:
        return json.loads(CALIBRATION_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _auctions(con, platform: str, league_id: str, season: int) -> list[dict]:
    """Walk-forward predictions for every contested main-run auction (legs with history)."""
    from fantasy_model import dynamics

    bt = bids.bid_table(con, platform, league_id, season)
    alive = bids.alive_by_leg(con, platform, league_id, season)
    runs = bids.main_runs(bt)
    legs = sorted(runs.filter(pl.len().over("leg") >= 10)["leg"].unique().to_list())
    names = dict(
        con.execute(
            "SELECT team_id, coalesce(team_name, owner_name) FROM team WHERE platform = ? "
            "AND league_id = ?",
            [platform, league_id],
        ).fetchall()
    )
    players = dict(
        con.execute("SELECT player_id, full_name FROM player_status_snapshot").fetchall()
    )
    out = []
    for leg in legs[2:]:  # need two earlier runs to fit on
        model = bids.fit(bt, alive, legs=[x for x in legs if x < leg], calibrated=False)
        run = runs.filter(pl.col("leg") == leg)
        mp = run["processed_at"][0]
        spent = dict(
            bt.filter(pl.col("won") & (pl.col("processed_at") < mp))
            .group_by("team_id")
            .agg(pl.col("bid").sum())
            .iter_rows()
        )
        faab = {t: 1000 - spent.get(t, 0) for t in sorted(alive[leg])}
        opps = [(t, f, 0.5) for t, f in faab.items()]
        won = run.filter(pl.col("won") & (pl.col("bid") >= MIN_WIN)).sort("bid", descending=True)
        for pid, q, winner, price in won.select(
            "player_id", "quality", "team_id", "bid"
        ).iter_rows():
            mx = bids.max_opponent_bids(model, q, opps)
            ranks = sorted(
                ((dynamics.expected_bid(model, q, t, f), t) for t, f in faab.items()), reverse=True
            )
            out.append(
                {
                    "leg": leg,
                    "player_id": pid,
                    "player": players.get(pid, pid),
                    "price": int(price),
                    "pred_median": float(np.median(mx)),
                    "pred_p10": float(np.quantile(mx, 0.1)),
                    "pred_p90": float(np.quantile(mx, 0.9)),
                    "winner": names.get(winner, winner),
                    "pred_winner": names.get(ranks[0][1], ranks[0][1]),
                    "winner_top3": winner in [t for _, t in ranks[:3]],
                    "pred_pay": ranks[0][0][1],
                    "_model": model,
                    "_q": q,
                    "_opps": opps,
                }
            )
    return out


def _score(auctions: list[dict], k: float, s: float, n_sims: int = 20000) -> tuple[float, float]:
    """(mean CRPS, 80% coverage) with spread x k and bid level x s."""
    crps, hits = [], []
    for a in auctions:
        m = dict(a["_model"], resid_sd=a["_model"]["resid_sd"] * k, level=s)
        mx = bids.max_opponent_bids(m, a["_q"], a["_opps"], n_sims=n_sims)
        crps.append(bids.crps(mx, a["price"]))
        lo, hi = np.quantile(mx, [0.1, 0.9])
        hits.append(lo <= a["price"] <= hi)
    return (float(np.mean(crps)), float(np.mean(hits))) if auctions else (float("nan"),) * 2


def my_claims(con, platform: str, league_id: str, season: int) -> list[dict]:
    """Our waiver claims: what we bid, whether we won, and what it took."""
    rows = con.execute(
        """WITH c AS (
             SELECT t.week, t.team_id, t.bid, t.status = 'complete' AS won, t.processed_at,
                    p.player_id
             FROM txn t JOIN txn_player p USING (platform, league_id, transaction_id)
             WHERE t.platform = ? AND t.league_id = ? AND t.season = ? AND t.type = 'waiver'
               AND p.action = 'add')
           SELECT c.week, c.player_id, c.bid, c.won,
                  (SELECT max(w.bid) FROM c w WHERE w.player_id = c.player_id
                     AND w.processed_at = c.processed_at AND w.won) AS winning_bid
           FROM c JOIN team tm ON tm.team_id = c.team_id AND tm.is_me
             AND tm.platform = ? AND tm.league_id = ?
           ORDER BY c.processed_at DESC, c.bid DESC""",
        [platform, league_id, season, platform, league_id],
    ).fetchall()
    names = dict(con.execute("SELECT player_id, full_name FROM player_status_snapshot").fetchall())
    return [
        {"week": w, "player": names.get(p, p), "bid": b, "won": won, "winning_bid": wb}
        for w, p, b, won, wb in rows
    ]


def build(con, platform: str, league_id: str, season: int, save: bool = True) -> dict:
    from fantasy_model import evaluate, projection_model

    auctions = _auctions(con, platform, league_id, season)
    raw_crps, raw_cov = _score(auctions, 1.0, 1.0)
    grid = {(k, s): _score(auctions, k, s, GRID_SIMS)[0] for k in SPREAD_GRID for s in LEVEL_GRID}
    spread_k, level_s = min(grid, key=grid.get) if auctions else (1.0, 1.0)
    new_crps, new_cov = _score(auctions, spread_k, level_s)
    ratios = [a["price"] / a["pred_pay"] for a in auctions if a["pred_pay"] > 0]
    pay_scale = float(np.clip(np.median(ratios), *CLAMP)) if ratios else None

    last_week = projection_model.complete_through(con, season) - 1
    chop = evaluate.sim_validation(con, platform, league_id, season, list(range(1, last_week + 1)))

    by_leg = []
    for leg in sorted({a["leg"] for a in auctions}):
        al = [a for a in auctions if a["leg"] == leg]
        by_leg.append(
            {
                "leg": leg,
                "n": len(al),
                "covered": sum(a["pred_p10"] <= a["price"] <= a["pred_p90"] for a in al),
                "bias": float(np.mean([a["pred_median"] - a["price"] for a in al])),
                "winner_hits": sum(a["pred_winner"] == a["winner"] for a in al),
                "winner_top3": sum(a["winner_top3"] for a in al),
            }
        )
    calibration = {
        "spread_k": float(spread_k),
        "level_s": float(level_s),
        "pay_scale": pay_scale,
        "auctions": len(auctions),
        "crps_before": raw_crps,
        "crps_after": new_crps,
        "coverage_before": raw_cov,
        "coverage_after": new_cov,
        "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if save and auctions:
        CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        CALIBRATION_PATH.write_text(json.dumps(calibration, indent=2))
    latest = max((a["leg"] for a in auctions), default=None)
    clean = [{k: v for k, v in a.items() if not k.startswith("_")} for a in auctions]
    return {
        "latest_leg": latest,
        "latest": [a for a in clean if a["leg"] == latest],
        "by_leg": by_leg,
        "summary": {
            "auctions": len(auctions),
            "coverage": raw_cov,
            "median_bias": float(np.median([a["pred_median"] - a["price"] for a in auctions]))
            if auctions
            else None,
            "winner_hit": float(np.mean([a["pred_winner"] == a["winner"] for a in auctions]))
            if auctions
            else None,
            "winner_top3": float(np.mean([a["winner_top3"] for a in auctions]))
            if auctions
            else None,
        },
        "chop": {
            "weeks": chop["weeks"].to_dicts(),
            "summary": chop["summary"],
        },
        "my_claims": my_claims(con, platform, league_id, season),
        "calibration": calibration,
    }
