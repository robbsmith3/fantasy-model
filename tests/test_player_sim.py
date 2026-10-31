import numpy as np
import polars as pl

from fantasy_model import player_sim as ps


def _bank():
    rng = np.random.default_rng(0)
    r = np.sort(rng.gamma(2.0, 0.5, 5000))  # mean 1, right-skewed like real outcomes
    return {(pos, b): r / r.mean() for pos in ("QB", "RB", "WR", "TE") for b in range(7)}


def test_draws_keep_the_projection_mean_and_include_dnp_zeros():
    players = pl.DataFrame({"position": ["WR"], "nfl_team": ["KC"], "mu": [7.0]})
    d = ps.sample_players(players, _bank(), 40000, np.random.default_rng(1))[:, 0]
    assert abs(d.mean() - 7.0 * (1 - ps.dnp_prob(7.0))) < 0.15
    assert 0.02 < (d == 0).mean() < 0.04  # DNP mass for a 5-9 pt projection


def test_qb_and_his_receiver_move_together_but_other_teams_do_not():
    players = pl.DataFrame(
        {"position": ["QB", "WR", "WR"], "nfl_team": ["KC", "KC", "BUF"], "mu": [20.0, 15.0, 15.0]}
    )
    d = ps.sample_players(players, _bank(), 40000, np.random.default_rng(2))
    same = np.corrcoef(d[:, 0], d[:, 1])[0, 1]
    other = np.corrcoef(d[:, 0], d[:, 2])[0, 1]
    assert 0.15 < same < 0.4 and abs(other) < 0.03


def test_lineup_totals_sum_starters():
    players = pl.DataFrame(
        {
            "player_id": ["a", "b"],
            "position": ["RB", "RB"],
            "nfl_team": ["X", "Y"],
            "mu": [10.0, 5.0],
        }
    )
    sims = ps.simulate_lineups(players, {"t1": ["a", "b"], "t2": ["a"]}, _bank(), n_sims=20000)
    assert sims["t1"].mean() > sims["t2"].mean() > 9.0
