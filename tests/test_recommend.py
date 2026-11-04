from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from fantasy_model import recommend
from fantasy_model.recommend import best_bid, plan_outcomes


def test_free_money_bids_just_above_the_field():
    opp = np.sort(np.full(1000, 100.0))  # everyone else tops out at exactly $100
    bid, p_get, _ = best_bid(opp, d_p_win=0.10, lam=0.0, faab=500)
    assert bid == 101 and p_get == 1.0


def test_expensive_money_means_no_bid():
    opp = np.sort(np.full(1000, 100.0))
    bid, _, ev = best_bid(opp, d_p_win=0.01, lam=0.001, faab=500)  # $101 costs 10x the gain
    assert bid == 0 and ev == 0.0


def test_never_bids_more_than_faab_and_trades_off_probability():
    opp = np.sort(np.random.default_rng(0).uniform(0, 400, 5000))
    bid, p_get, ev = best_bid(opp, d_p_win=0.08, lam=0.0002, faab=300)
    assert 0 < bid <= 300 and 0 < p_get < 1 and ev > 0


def test_denial_raises_the_bid_for_a_player_rivals_would_use_against_us():
    rng = np.random.default_rng(0)
    opp = np.sort(rng.uniform(0, 300, 5000))
    plain, _, _ = best_bid(opp, d_p_win=0.03, lam=0.0002, faab=500)
    harm = np.full(5000, 0.02)  # whoever outbids us gains enough to cost us 2 win pts
    blocked, _, ev = best_bid(opp, d_p_win=0.03, lam=0.0002, faab=500, deny_sorted=harm)
    assert blocked > plain and ev > 0


def test_zero_denial_matches_plain_bid():
    opp = np.sort(np.random.default_rng(1).uniform(0, 300, 5000))
    assert best_bid(opp, 0.03, 0.0002, 500) == best_bid(opp, 0.03, 0.0002, 500, np.zeros(5000))


def test_bid_curve_rises_from_zero_and_covers_the_recommended_bid():
    from fantasy_model.recommend import bid_curve

    opp = np.sort(np.random.default_rng(2).uniform(50, 250, 5000))
    c = bid_curve(opp, faab=500, bid=180)
    bids, ps = zip(*c, strict=True)
    assert bids[0] == 0 and ps[0] == 0.0 and bids[-1] >= 180
    assert all(b >= a for a, b in zip(ps, ps[1:], strict=False))  # P(get) never falls with bid
    assert max(bids) <= 500


def _claim(pid, drop, p):
    return {"player_id": pid, "drop_id": drop, "p_get": p}


def test_plan_outcomes_land_one_claim_per_shared_drop():
    out = plan_outcomes([_claim("a", "x", 0.6), _claim("b", "x", 0.5), _claim("c", "y", 0.5)])
    assert sum(out.values()) == pytest.approx(1.0)
    assert out[(0, 2)] == pytest.approx(0.6 * 0.5)  # a lands, so b fails whether or not it won
    assert out[(1,)] == pytest.approx(0.4 * 0.5 * 0.5)
    assert not any(0 in k and 1 in k for k in out)


def test_claim_plan_backs_up_a_claim_by_sharing_its_drop(monkeypatch):
    # One expendable player (bn), and too little FAAB for two separate top bids.
    vals = pl.DataFrame(
        {
            "player_id": ["q", "w", "bn", "a", "b"],
            "full_name": ["Q", "W", "Bench", "A", "B"],
            "position": ["QB", "WR", "WR", "WR", "WR"],
            "nfl_team": ["X"] * 5,
            "injury_status": [None] * 5,
            "owner_team_id": ["me", "me", "me", None, None],
            "pts_w1": [20.0, 5.0, 1.0, 20.0, 18.0],
        }
    )
    sheet = pl.DataFrame({"player_id": ["a", "b"], "player": ["A", "B"], "pos": ["WR", "WR"]})
    monkeypatch.setattr(
        recommend.survival,
        "pickup_delta",
        lambda mu, i, gains, *a: {"d_p_win": float(gains.sum()) / 100},
    )
    field = np.random.default_rng(0).uniform(0, 200, 2000)
    plan = recommend.claim_plan(
        sheet, vals, SimpleNamespace(me="me", slots=["QB", "WR", "WR", "BN"]), [1],
        np.zeros((2, 2)), 0, 1.0, None, 0, {"a": field, "b": field}, 0.0, 150,
    ).to_dicts()  # fmt: skip
    assert [p["player"] for p in plan] == ["A", "B"]
    assert plan[0]["backup_for"] is None and plan[1]["backup_for"] == "A"
    assert plan[0]["drop"] == plan[1]["drop"] == "Bench"
    assert plan[1]["bid"] < plan[0]["bid"] <= 150  # the higher bid runs first; only one is charged
    assert plan[0]["bid"] + plan[1]["bid"] > 150  # the point: a separate drop couldn't afford both


def test_market_outlook_ranks_likely_claims_and_skips_qb_def_and_owned():
    vals = pl.DataFrame(
        {
            "player_id": ["star", "depth", "qb", "dst", "mine", "hurt"],
            "full_name": ["Star", "Depth", "Qb", "Dst", "Mine", "Hurt"],
            "position": ["WR", "WR", "QB", "DEF", "WR", "RB"],
            "nfl_team": ["X"] * 6,
            "injury_status": [None] * 6,
            "owner_team_id": [None, None, None, None, "t", None],
            "ros_pts": [50.0, 20.0, 90.0, 40.0, 60.0, 0.0],
        }
    )
    model = {
        "feats": (), "w_part": np.zeros(8), "w_amt": np.zeros(8), "team_part": {}, "team_amt": {},
        "resid_sd": 0.3,
    }  # fmt: skip
    monkey = recommend.bids._x
    recommend.bids._x = lambda q, *a: np.array([q, *[0.0] * 7]) if q is not None else np.zeros(8)
    model["w_part"][0], model["w_amt"][0] = 1.0, 1.0
    try:
        out = recommend.market_outlook(
            vals, model, {"star": 0.0, "depth": -3.0}, [("t", 1000, 0.5), ("u", 100, 0.5)],
            ["WR", "BN"], {"t": "Team T", "u": "Team U"},
        ).to_dicts()  # fmt: skip
    finally:
        recommend.bids._x = monkey
    assert [r["player"] for r in out] == ["Star", "Depth"]  # no QB, DEF, owned or zero-value
    assert out[0]["p_claimed"] > out[1]["p_claimed"] > 0
    assert out[0]["bid_lo"] <= out[0]["bid"] <= out[0]["bid_hi"] <= 1000
    assert out[0]["rival"] == "Team T"  # the richer team usually tops the bids
