import numpy as np
import polars as pl

from fantasy_model import bids


def test_quality_shrinks_this_season_toward_last_season():
    assert bids.quality(20.0, 3.0, 1) == (20.0 * 4 + 3.0) / 5  # one bad week barely moves a star
    assert bids.quality(None, 30.0, 2) == 15.0  # rookie: this season only
    assert bids.quality(None, 0.0, 0) is None  # DEF / no data


def test_p_win_counts_ties_as_half():
    opp = np.array([0.0, 100.0, 200.0, 300.0])
    assert bids.p_win(150, opp) == 0.5
    assert bids.p_win(200, opp) == 0.5 + 0.5 * 0.25


def test_better_players_draw_higher_max_bids():
    model = {
        "w_part": np.array([-3.0, 0.15, 0.7]),
        "w_amt": np.array([-6.0, 0.22, 0.75]),
        "resid_sd": 1.5,
        "feats": (),
        "team_part": {},
        "team_amt": {"a": 1.0},
    }
    faab = [("a", 900, 0.5), ("b", 600, 0.5), ("c", 300, 0.5), ("d", 100, 0.5)]
    star = np.median(bids.max_opponent_bids(model, 20.0, faab))
    scrub = np.median(bids.max_opponent_bids(model, 6.0, faab))
    assert star > scrub
    assert bids.max_opponent_bids(model, 20.0, faab).max() <= 900  # never above anyone's FAAB


def test_fit_recovers_participation_direction():
    rng = np.random.default_rng(1)
    rows, alive = [], {1: {str(t) for t in range(10)}}
    for i, q in enumerate(rng.uniform(4, 22, 40)):
        for t in range(10):
            if rng.random() < 1 / (1 + np.exp(-(-4 + 0.25 * q))):
                share = float(np.clip(np.exp(-5 + 0.2 * q + 0.3 * rng.standard_normal()), 0, 1))
                rows.append(("t", 1, str(t), False, int(share * 1000), 0, f"p{i}", 1000, q, share))
    bt = pl.DataFrame(
        rows,
        schema=[
            "transaction_id",
            "leg",
            "team_id",
            "won",
            "bid",
            "processed_at",
            "player_id",
            "faab_before",
            "quality",
            "share",
        ],
        orient="row",
    )
    m = bids.fit(bt, alive)
    assert m["w_part"][1] > 0 and m["w_amt"][1] > 0


def test_crps_rewards_sharp_correct_forecasts():
    rng = np.random.default_rng(0)
    sharp = rng.normal(100, 10, 5000)
    wide = rng.normal(100, 80, 5000)
    off = rng.normal(300, 10, 5000)
    assert bids.crps(sharp, 100) < bids.crps(wide, 100) < bids.crps(off, 100)


def test_team_effects_are_shrunk_and_raise_an_aggressive_teams_bids():
    rows, alive = [], {1: {"hi", "lo"}, 2: {"hi", "lo"}}
    for lg in (1, 2):
        for i in range(30):
            q = 6 + i * 0.5
            rows.append(("t", lg, "hi", False, 300, lg, f"p{lg}{i}", 1000, q, 0.3))
            rows.append(("t", lg, "lo", False, 30, lg, f"p{lg}{i}", 1000, q, 0.03))
    bt = pl.DataFrame(
        rows,
        schema=["transaction_id", "leg", "team_id", "won", "bid", "processed_at", "player_id",
                "faab_before", "quality", "share"],
        orient="row",
    )  # fmt: skip
    m = bids.fit(bt, alive)
    assert m["team_amt"]["hi"] > 0 > m["team_amt"]["lo"]
    assert abs(m["team_amt"]["hi"]) < np.log(10) / 2  # shrunk toward 0 (true gap is log 10)


def _toy_bids():
    rng = np.random.default_rng(3)
    rows, alive = [], {1: {str(t) for t in range(6)}}
    for i, q in enumerate(rng.uniform(6, 20, 30)):
        for t in range(6):
            if rng.random() < 0.4:
                share = float(np.clip(np.exp(-4 + 0.15 * q + 0.4 * rng.standard_normal()), 0, 1))
                rows.append(("t", 1, str(t), False, int(share * 1000), 0, f"p{i}", 1000, q, share))
    schema = ["transaction_id", "leg", "team_id", "won", "bid", "processed_at", "player_id",
              "faab_before", "quality", "share"]  # fmt: skip
    return pl.DataFrame(rows, schema=schema, orient="row"), alive


def test_fit_applies_the_weekly_review_calibration_only_when_asked(tmp_path):
    import json

    from fantasy_model import review

    bt, alive = _toy_bids()
    raw = bids.fit(bt, alive, calibrated=False)
    review.CALIBRATION_PATH.write_text(json.dumps({"spread_k": 1.5, "level_s": 1.2}))
    cal = bids.fit(bt, alive)
    assert np.isclose(cal["resid_sd"], raw["resid_sd"] * 1.5) and cal["level"] == 1.2
    assert "level" not in raw
    faab = [("a", 900, 0.5), ("b", 600, 0.5)]
    hi = np.median(bids.max_opponent_bids(dict(raw, level=1.5), 18.0, faab))
    assert hi > np.median(bids.max_opponent_bids(raw, 18.0, faab))
