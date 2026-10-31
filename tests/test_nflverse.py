import polars as pl

from fantasy_model.nflverse import ID_COLS, _clean_ids


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
