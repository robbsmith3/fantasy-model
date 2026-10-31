import numpy as np

from fantasy_model import survival as sv


def test_lowest_team_is_chopped_when_noise_is_negligible():
    mu = np.array([[100.0, 100.0], [120.0, 120.0], [140.0, 140.0]])
    r = sv.simulate(mu, sigma=0.001, n_sims=200)
    assert r["p_chop"][0, 0] == 1.0 and r["p_chop"][1, 1] == 1.0
    assert r["p_win"].tolist() == [0.0, 0.0, 1.0]
    assert r["exp_weeks"].tolist() == [0.0, 1.0, 2.0]


def test_probabilities_are_consistent():
    mu = np.full((5, 4), 110.0)
    r = sv.simulate(mu, sigma=20.0, n_sims=4000)
    assert np.allclose(r["p_chop"].sum(axis=0), 1.0)  # exactly one chop per week
    assert np.isclose(r["p_win"].sum(), 1.0)
    assert np.allclose(r["p_win"], 0.2, atol=0.03)  # equal teams -> equal odds


def test_pickup_delta_is_positive_and_zero_for_no_gain():
    mu = np.full((4, 3), 110.0)
    noise = sv.common_noise(4, 3, n_sims=4000)
    assert sv.pickup_delta(mu, 0, np.array([10.0]), 20.0, noise)["d_p_win"] > 0.05
    assert sv.pickup_delta(mu, 0, np.array([0.0]), 20.0, noise)["d_p_win"] == 0.0


def test_extend_horizon_pads_with_team_mean():
    out = sv.extend_horizon(np.array([[100.0, 120.0]]), 4)
    assert out.tolist() == [[100.0, 120.0, 110.0, 110.0]]
