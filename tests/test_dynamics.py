import numpy as np
import polars as pl

from fantasy_model import dynamics, survival

MODEL = {
    "w_part": np.array([-1.0, 0.1, 0.0]),
    "w_amt": np.array([-2.0, 0.05, 0.0]),
    "resid_sd": 0.5,
    "feats": (),
    "team_part": {},
    "team_amt": {},
}


def _vals():
    # Team "rich" has a weak WR slot, "poor" is full; free agent star WR "s".
    return pl.DataFrame(
        {
            "player_id": ["r1", "p1", "s"],
            "full_name": ["R1", "P1", "Star"],
            "position": ["WR", "WR", "WR"],
            "nfl_team": ["A", "B", "C"],
            "injury_status": [None] * 3,
            "owner_team_id": ["rich", "poor", None],
            "pts_w1": [5.0, 25.0, 20.0],
            "ros_pts": [5.0, 25.0, 20.0],
        }
    )


def test_star_goes_to_the_team_that_needs_him_and_can_pay():
    got, spent = dynamics.allocate(
        _vals(), ["WR", "BN"], [1], ["s"], ["rich", "poor"],
        {"rich": 900, "poor": 900}, MODEL, {"s": 18.0}, {},
    )  # fmt: skip
    assert got == {"rich": 15.0}  # poor's WR (25) already beats him: no need, no bid
    assert 0 < spent["rich"] <= 900


def test_simulated_faab_stops_a_team_collecting_after_its_budget_runs_out():
    # Team 1 (110) is chopped first; team 0 (115) gets +30/wk from that dump if it can pay $1200.
    # Fully paid: 145 beats team 2 (140). With only $600 it gets half: 130 and it loses.
    mu = np.array([[115.0] * 3, [110.0] * 3, [140.0] * 3])
    gain = np.zeros((3, 3))
    gain[1, 0] = 30.0
    spend = np.zeros((3, 3))
    spend[1, 0] = 1200.0
    dumps = {"first": gain, "rest": gain, "spend_first": spend, "spend_rest": spend,
             "faab": np.array([600.0, 0.0, 0.0]), "now": np.zeros(3)}  # fmt: skip
    noise = survival.common_noise(3, 3, n_sims=4000)
    half = survival.simulate(mu, 2.0, noise=noise, drift=False, dumps=dumps)
    full = survival.simulate(
        mu, 2.0, noise=noise, drift=False, dumps=dict(dumps, faab=np.array([1200.0, 0, 0]))
    )
    assert full["p_win"][0] > 0.9 and half["p_win"][0] < 0.1
