import json

from fantasy_model import value

PPR = {"rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "pass_yd": 0.04, "pass_td": 4.0, "pass_int": -2.0}


def test_score_uses_league_rules_and_ignores_unscored_stats():
    stats = {"rec": 5, "rec_yd": 60, "rec_td": 1, "pts_ppr": 99.0, "adp_dd_ppr": 12}
    assert value.score(stats, PPR) == 17.0


def test_score_applies_league_specific_interception_penalty():
    stats = {"pass_yd": 250, "pass_td": 2, "pass_int": 1}
    assert value.score(stats, PPR) == 16.0  # Sleeper's preset (-1/INT) would say 17


def test_player_values_sums_weeks_zero_fills_byes_and_marks_owners(con):
    con.execute(
        "INSERT INTO league (platform, league_id, season, scoring) VALUES ('sleeper','L',2026,?)",
        [json.dumps(PPR)],
    )
    for wk, pid, rec in [(9, "A", 5), (10, "A", 3), (9, "B", 2)]:
        con.execute(
            "INSERT INTO projection_snapshot VALUES ('s1','sleeper',2026,?,?,'WR','KC',?)",
            [wk, pid, json.dumps({"rec": rec})],
        )
    for pid in ("A", "B"):
        con.execute(
            "INSERT INTO player_status_snapshot (snapshot_id, platform, player_id, full_name)"
            " VALUES ('p1','sleeper',?,?)",
            [pid, f"Player {pid}"],
        )
    con.execute("INSERT INTO roster_snapshot VALUES ('r1','sleeper','L','7','B',true)")

    v = value.player_values(con, "sleeper", "L", [9, 10])
    rows = {r["player_id"]: r for r in v.iter_rows(named=True)}
    assert rows["A"]["ros_pts"] == 8.0 and rows["A"]["owner_team_id"] is None
    assert rows["B"]["pts_w10"] == 0.0 and rows["B"]["owner_team_id"] == "7"
    assert v["player_id"].to_list() == ["A", "B"]
