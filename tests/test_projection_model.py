import polars as pl

from fantasy_model import projection_model as pm


def _setup(con):
    """Player P (WR, team A) scores 10, 20, 30 in weeks 1-3; team A plays weeks 1-5."""
    con.execute(
        """CREATE TABLE nfl_player_stats AS SELECT * FROM (VALUES
           ('P','WR',2026,1::DOUBLE,'A','B',10.0,5,0,0.2,0.3,0.4,0,'REG'),
           ('P','WR',2026,2::DOUBLE,'A','B',20.0,7,0,0.25,0.3,0.5,0,'REG'),
           ('P','WR',2026,3::DOUBLE,'A','B',30.0,9,0,0.3,0.3,0.6,0,'REG'))
           t(player_id, position, season, week, team, opponent_team, fantasy_points_ppr,
             targets, carries, target_share, air_yards_share, wopr, attempts, season_type)"""
    )
    con.execute(
        """CREATE TABLE nfl_ff_opportunity AS SELECT 'P' AS player_id, 2026 AS season,
           w AS week, 12.0 AS total_fantasy_points_exp FROM range(1, 4) t(w)"""
    )
    con.execute(
        """CREATE TABLE nfl_schedules AS SELECT 2026 AS season, w AS week, 'REG' AS game_type,
           'A' AS home_team, 'B' AS away_team, 45.0 AS total_line,
           CASE WHEN w <= 4 THEN 3.0 END AS spread_line,
           CASE WHEN w <= 3 THEN 7 END AS result FROM range(1, 6) t(w)"""
    )
    con.execute(
        """CREATE TABLE nfl_injuries (gsis_id VARCHAR, season DOUBLE, week DOUBLE,
           season_type VARCHAR, report_status VARCHAR, practice_status VARCHAR)"""
    )


def test_features_use_only_earlier_games(con):
    _setup(con)
    rows = pm.build_rows(con, [2026]).sort("week")
    wk3 = rows.filter(pl.col("week") == 3).row(0, named=True)
    assert wk3["ppr_l1"] == 20.0 and wk3["ppr_l3"] == 15.0  # weeks 1-2 only, not week 3's 30
    assert wk3["actual"] == 30.0 and wk3["weeks_since"] == 1
    assert rows.filter(pl.col("week") == 4)["actual"].item() == 0.0  # didn't play -> 0


def test_as_of_freezes_features_and_fills_missing_lines(con):
    _setup(con)
    assert pm.complete_through(con, 2026) == 4
    rows = pm.build_rows(con, [2026], as_of=(2026, 3))  # week 3 not yet known
    wk5 = rows.filter(pl.col("week") == 5).row(0, named=True)
    assert wk5["ppr_l1"] == 20.0  # week 3's game hidden
    assert wk5["weeks_since"] == 1  # measured from "now" (week 3), not from week 5
    assert wk5["spread"] == 3.0  # no line posted for week 5 -> season average
