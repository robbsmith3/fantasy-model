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
    }
    faab = [900, 600, 300, 100]
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
