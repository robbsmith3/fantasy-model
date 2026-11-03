import polars as pl
import pytest

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
           game_type VARCHAR, report_status VARCHAR, practice_status VARCHAR)"""
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


def _context_setup(con):
    """Week 3 target. A's usual WR (Star) and left tackle miss week 2; B's corner misses week 2."""
    con.execute(
        """CREATE TABLE nfl_player_stats (player_id TEXT, position TEXT, season INT, week DOUBLE,
           team TEXT, opponent_team TEXT, fantasy_points_ppr DOUBLE, targets INT, carries INT,
           target_share DOUBLE, air_yards_share DOUBLE, wopr DOUBLE, attempts INT,
           season_type TEXT)"""
    )
    for pid, wk, pts, tg in [("P", 1, 10.0, 4), ("Star", 1, 20.0, 10), ("P", 2, 18.0, 9),
                             ("P", 3, 30.0, 12), ("Star", 3, 25.0, 11)]:  # fmt: skip
        con.execute(
            "INSERT INTO nfl_player_stats VALUES (?, 'WR', 2026, ?, 'A', 'B', ?, ?, 0, 0.2, 0.2,"
            " 0.2, 0, 'REG')",
            [pid, wk, pts, tg],
        )
    con.execute(
        """CREATE TABLE nfl_ff_opportunity AS SELECT p AS player_id, 2026 AS season, w AS week,
           10.0 AS total_fantasy_points_exp FROM range(1, 4) t(w), (VALUES ('P'), ('Star')) v(p)"""
    )
    con.execute(
        """CREATE TABLE nfl_schedules AS SELECT 2026 AS season, w AS week, 'REG' AS game_type,
           'A' AS home_team, 'B' AS away_team, 45.0 AS total_line, 3.0 AS spread_line, 7 AS result
           FROM range(1, 4) t(w)"""
    )
    con.execute(
        """CREATE TABLE nfl_injuries (season DOUBLE, week DOUBLE, team TEXT, gsis_id TEXT,
           full_name TEXT, game_type TEXT, report_status TEXT, practice_status TEXT)"""
    )
    con.execute(
        """CREATE TABLE nfl_snap_counts (season INT, week INT, game_type TEXT, team TEXT,
           player TEXT, position TEXT, offense_pct DOUBLE, defense_pct DOUBLE)"""
    )
    snaps = [("A", "Left Tackle", "T", 1.0, 0.0, [1]),
             ("A", "Right Tackle", "T", 1.0, 0.0, [1, 2, 3]),
             ("B", "Corner", "CB", 0.0, 1.0, [1]),
             ("B", "Safety", "S", 0.0, 1.0, [1, 2, 3])]  # fmt: skip
    for t, n, pos, o, d, wks in snaps:
        for w in wks:
            con.execute(
                "INSERT INTO nfl_snap_counts VALUES (2026, ?, 'REG', ?, ?, ?, ?, ?)",
                [w, t, n, pos, o, d],
            )


def _wk3(con, player="P"):
    rows = pm.build_rows(con, [2026])
    out = pm.context_features(con, rows)
    return out.filter((pl.col("week") == 3) & (pl.col("player_id") == player)).row(0, named=True)


def test_tuesday_context_uses_only_earlier_games(con):
    _context_setup(con)
    r = _wk3(con)
    assert r["open_tgt_t"] == 5.0  # Star: 10 targets over 2 team games, absent from week 2
    assert r["ol_out_t"] == pytest.approx(0.5 / 1.5)  # LT 0.5 of the group's 1.5 usual snaps
    assert r["db_out_t"] == pytest.approx(0.5 / 1.5)
    # The target game itself can't move any feature: Star playing week 3 is what happened next.
    con.execute("UPDATE nfl_player_stats SET targets = 99 WHERE week = 3")
    con.execute("UPDATE nfl_snap_counts SET offense_pct = 0.01 WHERE week = 3 AND position = 'T'")
    again = _wk3(con)
    cols = [c for c in r if c not in ("actual", "played")]
    assert {c: r[c] for c in cols} == {c: again[c] for c in cols}


def test_target_week_report_only_moves_the_in_week_features(con):
    _context_setup(con)
    before = _wk3(con)
    con.execute(
        """INSERT INTO nfl_injuries VALUES (2026, 3, 'A', 'Star', 'Star', 'REG', NULL, NULL),
           (2026, 3, 'A', 'x', 'Right Tackle', 'REG', 'Out', NULL),
           (2026, 3, 'B', 'y', 'Safety', 'REG', 'Questionable', NULL)"""
    )
    after = _wk3(con)
    tue = [c for c in before if c.endswith("_t")]
    assert tue and all(after[c] == before[c] for c in tue)
    assert after["open_tgt_w"] == 0.0  # listed with no game status: Star is back
    assert after["ol_out_w"] == pytest.approx(1.5 / 1.5)  # LT still gone, RT now Out
    assert after["db_out_w"] == pytest.approx((0.5 + 0.15) / 1.5)


def test_injury_reports_load_from_game_type():
    # nflverse leaves season_type empty before 2025; game_type is filled for every season.
    import duckdb

    con = duckdb.connect()
    con.execute(
        """CREATE TABLE nfl_injuries AS SELECT 2024 AS season, 5 AS week, 'A' AS team,
           'x' AS gsis_id, 'X Y' AS full_name, 'REG' AS game_type, NULL AS season_type,
           'Out' AS report_status"""
    )
    assert pm._reports(con, [2024])["out"].to_list() == [1.0]


def test_defense_quality_adjusts_for_schedule_and_shrinks():
    # Week-1 hammering of D1 by a great offense is weaker evidence than a neutral blowout.
    games = pl.DataFrame({
        "season": [2026] * 4, "week": [1, 1, 2, 2], "position": ["WR"] * 4,
        "team": ["GOOD", "BAD", "GOOD", "BAD"],
        "opponent_team": ["D1", "D2", "D2", "D1"],
        "fantasy_points_ppr": [40.0, 10.0, 40.0, 10.0],
    })  # fmt: skip
    q = pm._defense_quality(games)
    wk2 = {r["opp"]: r["def_adj"] for r in q.filter(pl.col("week") == 2).iter_rows(named=True)}
    wk3 = {r["opp"]: r["def_adj"] for r in q.filter(pl.col("week") == 3).iter_rows(named=True)}
    # Each defense faced GOOD once and BAD once by week 3: schedule-adjusted, they're equal.
    assert abs(wk3["D1"] - wk3["D2"]) < 0.5
    assert abs(wk2["D1"]) < 40 - 25  # one game, heavily shrunk from the raw +15


def test_production_feature_set_is_unchanged_by_the_candidates():
    assert pm.feature_set([], in_week=False) == pm.FEATURES
    assert not set(pm.FEATURES) & {f for g in pm.CANDIDATE_GROUPS.values() for h in g for f in h}
