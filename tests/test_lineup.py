import numpy as np

from fantasy_model import lineup

SLOTS = ["QB", "RB", "WR", "FLEX", "BN", "BN"]


def test_assign_slots_orders_positions_then_flex_then_def():
    slots = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "DEF", "BN"]
    starters = [
        ("d", "DEF", 7), ("w3", "WR", 9), ("q", "QB", 20), ("r1", "RB", 15), ("w1", "WR", 14),
        ("r3", "RB", 8), ("t", "TE", 10), ("r2", "RB", 12), ("w2", "WR", 11),
    ]  # fmt: skip
    got = lineup.assign_slots(starters, slots)
    assert got == [
        ("QB", "q"), ("RB", "r1"), ("RB", "r2"), ("WR", "w1"), ("WR", "w2"), ("TE", "t"),
        ("FLEX", "w3"), ("FLEX", "r3"), ("DEF", "d"),
    ]  # fmt: skip


def test_legal_respects_slot_eligibility():
    ok = [("q", "QB"), ("r", "RB"), ("w", "WR"), ("t", "TE")]  # TE fills FLEX
    assert lineup.legal(ok, SLOTS)
    assert not lineup.legal([("q", "QB"), ("q2", "QB"), ("w", "WR"), ("t", "TE")], SLOTS)
    assert not lineup.legal(ok[:3], SLOTS)  # an empty slot is not a lineup


def _draws(n=20000):
    rng = np.random.default_rng(0)
    base = {"q": rng.normal(20, 3, n), "r": rng.normal(15, 3, n), "w": rng.normal(12, 3, n)}
    steady = rng.normal(10.0, 1.0, n)  # FLEX option A: steady
    boom = rng.normal(10.5, 12.0, n)  # FLEX option B: higher mean, boom-or-bust
    ids = ["q", "r", "w", "steady", "boom"]
    return ids, np.column_stack([base["q"], base["r"], base["w"], steady, boom])


def test_leader_prefers_the_steady_player_trailer_prefers_the_volatile_one():
    ids, d = _draws()
    pos = {"q": "QB", "r": "RB", "w": "WR", "steady": "WR", "boom": "WR"}
    mu = {"q": 20, "r": 15, "w": 12, "steady": 10.0, "boom": 10.5}
    ahead = np.full(d.shape[0], 50.0)  # we project ~57: steady keeps us above the line
    behind = np.full(d.shape[0], 66.0)  # we need a big week
    max_pts, safe = lineup.search(d, ids, pos, mu, ahead, SLOTS)
    assert "boom" in max_pts and "steady" in safe
    _, gamble = lineup.search(d, ids, pos, mu, behind, SLOTS)
    assert "boom" in gamble
