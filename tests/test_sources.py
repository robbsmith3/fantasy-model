from fantasy_model import espn


def _player(pid, pos_id, stats):
    return {"player": {"id": pid, "defaultPositionId": pos_id, "proTeamId": 33, "stats": stats}}


def test_espn_parse_maps_stat_ids_and_filters_source_season_week():
    stat = {"53": 6.0, "42": 70.5, "43": 0.5, "999": 3.0}
    players = [
        _player(1, 3, [
            {"statSourceId": 1, "statSplitTypeId": 1, "seasonId": 2026, "scoringPeriodId": 9,
             "stats": stat},
            {"statSourceId": 0, "statSplitTypeId": 1, "seasonId": 2026, "scoringPeriodId": 9,
             "stats": stat},  # actuals, not projections
            {"statSourceId": 1, "statSplitTypeId": 1, "seasonId": 2025, "scoringPeriodId": 9,
             "stats": stat},  # last season
            {"statSourceId": 1, "statSplitTypeId": 0, "seasonId": 2026, "scoringPeriodId": 0,
             "stats": stat},  # season total
        ]),
        _player(2, 16, [  # D/ST not mapped yet
            {"statSourceId": 1, "statSplitTypeId": 1, "seasonId": 2026, "scoringPeriodId": 9,
             "stats": stat},
        ]),
    ]  # fmt: skip
    rows = list(espn.parse(players, 2026, espn.PROJECTED, {9}))
    assert rows == [("1", 9, "WR", "BAL", {"rec": 6.0, "rec_yd": 70.5, "rec_td": 0.5})]


def test_fantasypros_assigns_week_from_schedule_and_aliases_team_codes(con, monkeypatch):
    import sys
    import types

    import polars as pl

    from fantasy_model import fantasypros

    con.execute(
        "CREATE TABLE nfl_schedules AS SELECT 2026 AS season, 9 AS week,"
        " DATE '2026-11-08' AS gameday, 'LA' AS home_team, 'SF' AS away_team"
    )
    kickoff = 1794168000  # 2026-11-08 13:20 ET
    rankings = pl.DataFrame(
        {
            "page": ["ppr-wr", "ppr-wr", "dst"],
            "fantasypros_id": [1, 2, 3],
            "player_name": ["Ram", "Niner", "Defense"],
            "team": ["LAR", "SF", "LAR"],
            "player_game_kickoff_ts": [kickoff, kickoff, kickoff],
            "ecr": [1.0, 2.0, 3.0],
            "sd": [0.5, 0.5, 0.5],
            "r2p_pts": [20.0, 15.0, 8.0],
        }
    )
    fake = types.SimpleNamespace(load_ff_rankings=lambda type: rankings)
    monkeypatch.setitem(sys.modules, "nflreadpy", fake)
    assert fantasypros.snapshot_projections(con, "s1", 2026) == 2  # DST page skipped
    rows = con.execute(
        "SELECT source_player_id, week, position FROM projection_snapshot ORDER BY 1"
    ).fetchall()
    assert rows == [("1", 9, "WR"), ("2", 9, "WR")]
