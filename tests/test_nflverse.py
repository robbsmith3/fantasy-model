import polars as pl

from fantasy_model.nflverse import ID_COLS, _clean_ids, _replace_seasons


def test_clean_ids_strips_float_suffix_and_fills_missing():
    df = pl.DataFrame(
        {
            "sleeper_id": [4046.0, None],
            "yahoo_id": ["30001", "30002"],
            "name": ["a", "b"],
            "extra": [1, 2],
        }
    )
    out = _clean_ids(df)
    assert out.columns == ID_COLS
    assert out["sleeper_id"].to_list() == ["4046", None]
    assert out["gsis_id"].null_count() == 2


def test_replace_seasons_keeps_other_seasons_and_adds_new_columns(con):
    old = pl.DataFrame({"season": [2024, 2025], "week": [1, 1], "pts": [1.0, 2.0]})
    _replace_seasons(con, "hist", old, [2024, 2025])

    new = pl.DataFrame(
        {"season": [2025, 2025], "week": [1, 2], "pts": [3.0, 4.0], "snaps": [10, 20]}
    )
    _replace_seasons(con, "hist", new, [2025])

    rows = con.execute("SELECT season, week, pts, snaps FROM hist ORDER BY season, week").fetchall()
    assert rows == [(2024, 1, 1.0, None), (2025, 1, 3.0, 10), (2025, 2, 4.0, 20)]
