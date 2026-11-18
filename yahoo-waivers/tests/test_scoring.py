import time

from fixtures import available, public, roster, settings, transactions

from waivers import public_data, scoring
from waivers.yahoo_parse import (
    Player,
    parse_players,
    parse_roster,
    parse_settings,
    parse_transactions,
)


def _inputs():
    info = parse_settings(settings())
    data = public()
    pool = [p for pos in ("QB", "RB", "WR", "TE") for p in parse_players(available(pos))]
    sigs = public_data.signals(data, info.scoring, info.current_week)
    opps = {w: public_data.opponents(data.schedule, w) for w in (10, 11, 12)}
    return info, parse_roster(roster()), pool, sigs, data, opps


def test_rising_player_from_a_chopped_team_tops_the_list_with_a_bench_drop():
    info, mine, pool, sigs, data, opps = _inputs()
    chopped = scoring.chopped_releases(parse_transactions(transactions(1000)), 1000)
    assert "20" in chopped
    recs = scoring.recommend(info, mine, pool, 80, sigs, public_data.byes(data.schedule), opps,
                             {}, chopped)  # fmt: skip
    top = recs[0]
    assert top.player.name == "Juliet Rising" and top.priority == 1
    assert top.drop.slot == "BN"  # never a starter while a bench player costs less
    assert "chopped" in top.rationale and "snap share" in top.rationale
    assert 0 <= top.bid_low <= top.bid_high <= 80
    assert [r.priority for r in recs] == list(range(1, len(recs) + 1)) and len(recs) <= 5


def test_signals_use_league_scoring_and_recent_window():
    info, *_ = _inputs()
    sig = public_data.signals(public(), info.scoring, 10)["20"]
    assert sig.games == 9 and sig.ppg_recent > sig.ppg
    assert sig.snap_recent > sig.snap and sig.rz_recent == 3
    assert public_data.signals(public(), info.scoring, 10)["5"].report == "Questionable"


def test_bye_and_injury_zero_out_weeks():
    p = Player("k", "x", "X", "AAA", "WR")
    assert scoring.project(p, None, 10, 0, {"AAA": 10}, {}, {}) == 0.0
    hurt = Player("k", "x", "X", "AAA", "WR", status="O")
    assert scoring.project(hurt, None, 10, 0, {}, {}, {}) == 0.0
    assert scoring.project(hurt, None, 10, 2, {}, {}, {}) > 0
    ir = Player("k", "x", "X", "AAA", "WR", status="IR")
    assert scoring.project(ir, None, 10, 2, {}, {}, {}) == 0.0


def test_lineup_fills_flex_with_the_best_leftover():
    players = [("a", "RB", 10), ("b", "RB", 9), ("c", "RB", 8), ("d", "WR", 5)]
    assert scoring.best_lineup(players, ["RB", "RB", "WR", "W/R/T", "BN"]) == 32


def test_faab_range_tiers_and_late_season():
    early, late = scoring.faab_range(6.0, 100, 2), scoring.faab_range(6.0, 100, 16)
    assert early[0] < early[1] and late[1] > early[1]
    assert scoring.faab_range(0.1, 100, 10) == (0, 1)
    assert scoring.faab_range(9.0, 10, 17)[1] <= 10


def test_chop_needs_a_burst_of_drops_from_one_team():
    now = int(time.time())
    moves = parse_transactions(transactions(now))
    assert len(scoring.chopped_releases(moves, now)) == 5
    assert scoring.chopped_releases(moves[:4], now) == set()  # four drops isn't a chop
    assert scoring.chopped_releases(moves, now + 30 * 86400) == set()  # too old
