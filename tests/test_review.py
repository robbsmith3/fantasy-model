from fantasy_model import review


def test_missing_or_corrupt_calibration_falls_back_to_defaults():
    assert review.load_calibration() == {}  # no file yet (tests use a temp path)
    review.CALIBRATION_PATH.write_text("{not json")
    assert review.load_calibration() == {}
    review.CALIBRATION_PATH.write_text('{"spread_k": 1.25}')
    assert review.load_calibration()["spread_k"] == 1.25
