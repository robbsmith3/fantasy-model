import time

from fixtures import available, roster, settings, team, transactions

from waivers.yahoo_parse import (
    parse_faab,
    parse_players,
    parse_roster,
    parse_settings,
    parse_transactions,
)


def test_settings_slots_scoring_and_faab():
    info = parse_settings(settings())
    assert info.current_week == 10 and info.uses_faab and info.waiver_type == "FR"
    assert info.slots.count("RB") == 2 and "W/R/T" in info.slots and info.slots.count("BN") == 3
    assert info.scoring[11] == 1.0 and info.scoring[6] == -2.0


def test_team_roster_and_pool():
    assert parse_faab(team()) == 80
    r = parse_roster(roster())
    assert len(r) == 11 and {p.slot for p in r} >= {"QB", "BN", "IR"}
    hurt = next(p for p in r if p.yahoo_id == "9")
    assert hurt.status == "IR" and hurt.bye == 14 and hurt.team == "AAA"
    wr = parse_players(available("WR"))
    assert [p.name for p in wr] == ["Juliet Rising"] and wr[0].slot is None


def test_transactions_are_drops_with_source_team():
    moves = parse_transactions(transactions(int(time.time())))
    assert len(moves) == 5 and {m.kind for m in moves} == {"drop"}
    assert {m.team_key for m in moves} == {"nfl.l.111.t.9"}
