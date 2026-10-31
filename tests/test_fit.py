import polars as pl

from fantasy_model import fit

SLOTS = ["QB", "RB", "WR", "FLEX", "BN", "BN"]


def test_best_lineup_fills_narrow_slots_first_then_flex():
    players = [("q", "QB", 20), ("r1", "RB", 15), ("r2", "RB", 12), ("w", "WR", 10), ("t", "TE", 9)]
    # QB 20 + RB 15 + WR 10 + FLEX best of (RB 12, TE 9) = 57
    assert fit.best_lineup(players, SLOTS) == 57


def test_best_lineup_leaves_slot_empty_when_no_eligible_player():
    assert fit.best_lineup([("q", "QB", 20)], SLOTS) == 20


def test_availability_curves():
    assert fit.availability(None, 0) == 1.0
    assert fit.availability("Out", 0) == 0.0 and fit.availability("Out", 3) == 1.0
    assert fit.availability("NA", 10) == 0.0


def test_roster_gain_counts_bye_cover_and_picks_cheapest_drop():
    values = pl.DataFrame(
        {
            "player_id": ["q", "r", "w", "x", "bn", "fa"],
            "full_name": ["Q", "R", "W", "X", "Bench", "FreeAgent"],
            "position": ["QB", "RB", "WR", "WR", "WR", "RB"],
            "nfl_team": ["A"] * 6,
            "injury_status": [None] * 6,
            "owner_team_id": ["me", "me", "me", "me", "me", None],
            "pts_w1": [20.0, 15.0, 10.0, 8.0, 1.0, 9.0],
            "pts_w2": [20.0, 0.0, 10.0, 8.0, 1.0, 9.0],  # R on bye in week 2
            "ros_pts": [40.0, 15.0, 20.0, 16.0, 2.0, 18.0],
        }
    )
    g = fit.roster_gain(values, "me", SLOTS, [1, 2]).row(0, named=True)
    # wk1: FA (9) beats X (8) at FLEX -> +1; wk2: FA covers R's bye at RB -> +9
    assert (g["gain_w1"], g["gain_w2"], g["drop_name"]) == (1.0, 9.0, "Bench")


def test_claim_gain_accounts_for_earlier_wins_and_reserved_drops():
    values = pl.DataFrame(
        {
            "player_id": ["q", "d1", "bn1", "bn2", "defA", "defB"],
            "full_name": ["Q", "D1", "B1", "B2", "DefA", "DefB"],
            "position": ["QB", "DEF", "WR", "WR", "DEF", "DEF"],
            "nfl_team": ["A"] * 6,
            "injury_status": [None] * 6,
            "owner_team_id": ["me", "me", "me", "me", None, None],
            "pts_w1": [20.0, 4.0, 1.0, 2.0, 9.0, 8.0],
            "ros_pts": [20.0, 4.0, 1.0, 2.0, 9.0, 8.0],
        }
    )
    slots = ["QB", "DEF", "BN", "BN"]
    gains, drop = fit.claim_gain(values, "me", slots, [1], "defB")
    # DefB (8) replaces D1 (4); dropping D1 or a bench WR costs nothing, so any of them ties.
    assert gains == [4.0] and drop in {"d1", "bn1", "bn2"}
    # If DefA (9) already won, DefB adds nothing; and bn1 is reserved for that claim.
    gains, drop = fit.claim_gain(
        values, "me", slots, [1], "defB", adds=["defA"], drops=["bn1"], no_drop={"bn1"}
    )
    assert gains == [0.0] and drop not in {"bn1", "defA"}
