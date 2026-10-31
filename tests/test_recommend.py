import numpy as np

from fantasy_model.recommend import best_bid


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
