import numpy as np
import pytest

from fantasy_model import faab_value as fv
from fantasy_model import recommend, sleeper


def _auctions(gain=4.0, p=0.9, log_share=-1.0):
    # Three teams, team 0 is us; pools 0-2 are chopped rosters, pool 3 is today's. One player each.
    shape = (4, 1, 3)
    g = np.full(shape, gain)
    pr = np.full(shape, p)
    pr[:, :, 0] = 0.0
    return fv.Auctions(pr, np.full(shape, log_share), g, 0.3, 1.0)


def _play(a, my_money, rival_money=500.0, rate=4.0, **kw):
    noise = np.random.default_rng(0).standard_normal((3000, 3, 2))
    mu = np.full((3, 2), 100.0)
    draws = fv._draws(a, 3000, 2)
    faab = np.array([my_money, rival_money, rival_money], dtype=float)
    return fv.play_season(mu, 20.0, noise, a, faab, 0, rate, 0, draws, **kw)


def test_money_buys_wins_and_can_be_spent():
    a = _auctions()
    poor, rich = _play(a, 0.0), _play(a, 1000.0)
    assert rich["p_win"] > poor["p_win"] and poor["my_wins"] == 0 < rich["my_wins"]
    assert rich["money_end"][0] <= 1000


def test_rivals_spend_only_what_they_have():
    run = _play(_auctions(log_share=2.0), 0.0, rival_money=100.0)  # they'd bid everything
    assert run["money_end"][1] >= 0 and run["spend"].sum() <= 200


def test_late_prices_cut_late_rival_spend():
    a = _auctions()
    assert (
        _play(a, 0.0, late_price=0.3)["spend"].sum() < _play(a, 0.0, late_price=1.0)["spend"].sum()
    )


def test_concave_curve_is_on_or_above_and_rising_at_a_slowing_rate():
    x = np.array([0.0, 25, 50, 75, 100])
    y = np.array([0.10, 0.20, 0.19, 0.27, 0.28])
    c = fv.concave_up_to(x, y)
    assert (c >= np.maximum.accumulate(y) - 1e-12).all()
    slopes = np.diff(c)
    assert (slopes >= 0).all() and (np.diff(slopes) <= 1e-12).all()


def test_cost_and_walk_away_read_the_curve():
    curve = np.linspace(0.0, 0.1, 101) ** 0.5  # value of $0..$100
    cost = fv.money_cost(curve, 100, 100)
    assert cost[0] == 0 and (np.diff(cost) >= 0).all()
    w = fv.walk_away(curve, 100, cost[60])
    assert w == 60 and fv.walk_away(curve, 100, 0.0) == 0


def test_best_bid_uses_the_money_curve_instead_of_the_flat_price():
    opp = np.sort(np.random.default_rng(0).uniform(0, 400, 5000))
    flat, *_ = recommend.best_bid(opp, 0.08, 0.0001, 500)
    # Money that gets steeply dearer past $100 should pull the bid down.
    cost = np.where(np.arange(501) > 100, 0.02 + (np.arange(501) - 100) * 0.001, 0.0)
    curved, *_ = recommend.best_bid(opp, 0.08, 0.0001, 500, cost=cost)
    assert curved < flat


def test_import_uses_an_old_snapshot_id_and_refuses_unfinished_leagues(monkeypatch):
    class Resp:
        def __init__(self, status):
            self.status = status

        def json(self):
            return {"season": "2025", "status": self.status, "settings": {"last_scored_leg": 2}}

    ids = []
    monkeypatch.setattr(sleeper, "sync_league", lambda con, sid, *a: ids.append(sid))
    monkeypatch.setattr(sleeper, "sync_week_scores", lambda con, sid, *a: 18)
    monkeypatch.setattr(sleeper, "sync_transactions", lambda con, sid, *a: 5)
    monkeypatch.setattr(sleeper.requests, "get", lambda *a, **k: Resp("complete"))
    out = sleeper.import_past_league(None, "L")
    assert ids == ["2025-12-31T00:00Z_import"] and out["team_weeks"] == 36
    monkeypatch.setattr(sleeper.requests, "get", lambda *a, **k: Resp("in_season"))
    with pytest.raises(ValueError):
        sleeper.import_past_league(None, "L")


def test_season_gains_keep_byes_out_of_later_weeks():
    from fantasy_model import fit

    g = fit.season_gains([4.0, 0.0, 4.0, 4.0], [10.0, 0.0, 10.0, 10.0], 6)
    assert list(g) == [4.0, 0.0, 4.0, 4.0, 4.0, 4.0]  # week 2 was his bye
    assert list(fit.season_gains([3.0, 1.0], [5.0, 5.0], 1)) == [3.0]


def test_claim_rows_score_every_pool_player():
    from fantasy_model import bids

    rows = bids._claim_rows(
        ["a", "b"], {"a": 15.0, "b": 5.0}.get, {"a": "WR", "b": "WR"},
        [[("x", "WR", 8.0)]], ["WR", "BN"], 400.0,
    )  # fmt: skip
    by = {r["player_id"]: r for r in rows}
    assert by["a"]["pool_rank"] == 1.0 and by["b"]["pool_rank"] == 0.0
    assert by["a"]["top_need"] > by["b"]["top_need"] == 0.0


def test_one_chop_left_still_plays():
    a = _auctions()
    noise = np.random.default_rng(0).standard_normal((500, 3, 1))
    run = fv.play_season(
        np.full((3, 1), 100.0), 20.0, noise, a, np.array([100.0, 100, 100]), 0, 4.0, 1,
        fv._draws(a, 500, 1),
    )  # fmt: skip
    assert 0 <= run["p_win"] <= 1
