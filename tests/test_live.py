import numpy as np

from fantasy_model import live


def _event(teams, state, period=0, clock="0:00"):
    return {
        "competitions": [
            {
                "status": {"type": {"state": state, "completed": state == "post"},
                           "period": period, "displayClock": clock},
                "competitors": [{"team": {"abbreviation": t}} for t in teams],
            }
        ]
    }  # fmt: skip


def test_remaining_share_from_game_clock():
    sb = {
        "events": [
            _event(["KC", "BUF"], "pre"),
            _event(["WSH", "DAL"], "in", period=3, clock="7:30"),  # 15 + 7.5 of 60 min left
            _event(["GB", "CHI"], "post", period=4),
            _event(["NE", "NYJ"], "in", period=5, clock="4:00"),  # overtime: nothing left
        ]
    }
    left = live.remaining_share(sb)
    assert left["KC"] == 1.0 and left["GB"] == 0.0 and left["NE"] == 0.0
    assert left["WAS"] == 22.5 / 60  # ESPN WSH -> Sleeper WAS


def test_finals_add_only_the_unplayed_share():
    current = np.array([20.0, 5.0, 0.0])
    draws = np.full((100, 3), 10.0)
    share = np.array([0.0, 0.5, 1.0])  # finished, half left, not started
    finals = live.simulate_finals(current, draws, share, [[0, 1], [2]])
    assert finals[0].tolist() == [30.0, 10.0]  # 20 + (5 + 0.5*10); 0 + 10
