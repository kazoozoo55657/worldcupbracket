"""Tests for the group-driven bracket: structure resolution + rankings stars."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worldcup import bracket_structure as bs
from worldcup import rankings


def test_round_of():
    assert bs.round_of(73) == "R32"
    assert bs.round_of(88) == "R32"
    assert bs.round_of(89) == "R16"
    assert bs.round_of(97) == "QF"
    assert bs.round_of(101) == "SF"
    assert bs.round_of(104) == "F"


def test_third_place_slots_are_eight_and_away_side():
    assert len(bs.THIRD_PLACE_SLOTS) == 8
    # every third-place slot is the away side in our structure
    for m in bs.R32_MATCHES:
        if m["no"] in bs.THIRD_PLACE_SLOTS:
            assert m["away"][0] == "3" and m["home"][0] != "3"


def test_resolve_fills_winner_runner_slots():
    # group winners/runners by code; e.g. match 73 = 2A vs 2B, match 75 = 1F vs 2C
    gw = {"F": 100, "C": 200}
    gr = {"A": 1, "B": 2, "C": 201, "F": 101}
    parts, wins = bs.resolve(gw, gr, {}, {})
    assert parts[73] == (1, 2)            # 2A vs 2B
    assert parts[75] == (100, 201)        # 1F vs 2C
    assert parts[76] == (200, 101)        # 1C vs 2F
    assert wins[73] is None               # no winner picked yet


def test_build_and_propagate_champion():
    # Minimal: fill match 73 (2A vs 2B) and 75 (1F vs 2C); pick winners; check R16 feed 90.
    gw = {"F": 100, "C": 200}
    gr = {"A": 1, "B": 2, "C": 201, "F": 101}
    # match_choice: 73 -> team1, 75 -> team100; R16 match 90 feeds (73,75) -> pick 100
    mc = {73: 1, 75: 100, 90: 100}
    rw, parts, wins = bs.build_from_match_choices(gw, gr, {}, mc)
    assert wins[73] == 1 and wins[75] == 100
    assert parts[90] == (1, 100)          # winners of 73 and 75
    assert wins[90] == 100
    assert rw["R32"] == {1, 100}
    assert rw["R16"] == {100}


def test_build_drops_stale_downstream_winner():
    gw = {"F": 100, "C": 200}
    gr = {"A": 1, "B": 2, "C": 201, "F": 101}
    # pick R16 winner 999 that isn't a participant of match 90 -> dropped
    mc = {73: 1, 75: 100, 90: 999}
    rw, parts, wins = bs.build_from_match_choices(gw, gr, {}, mc)
    assert wins[90] is None
    assert "R16" not in rw or 999 not in rw.get("R16", set())


def test_third_place_slot_participant():
    gw = {"E": 50}
    parts, wins = bs.resolve(gw, {}, {74: 77}, {})  # match 74 = 1E vs 3rd(...)
    assert parts[74] == (50, 77)


def test_group_medals_top3_by_rank():
    # Group E: Germany(10) > Ecuador(23) > Ivory Coast(33) > Curacao(82)
    teams = [{"id": 1, "name": "Germany"}, {"id": 2, "name": "Ecuador"},
             {"id": 3, "name": "Ivory Coast"}, {"id": 4, "name": "Curaçao"}]
    medals = rankings.group_medals(teams)
    assert medals[1] == "gold" and medals[2] == "silver"
    assert medals[3] == "bronze" and medals[4] is None


def test_group_medals_congo_over_uzbekistan():
    # Group K bronze should be Congo DR (46) over Uzbekistan (50)
    teams = [{"id": 1, "name": "Portugal"}, {"id": 2, "name": "Colombia"},
             {"id": 3, "name": "Congo DR"}, {"id": 4, "name": "Uzbekistan"}]
    medals = rankings.group_medals(teams)
    assert medals[3] == "bronze" and medals[4] is None


def test_flags():
    from worldcup import flags
    assert flags.code_of("Curaçao") == "cw"
    assert flags.code_of("United States") == "us"
    assert flags.flag_img("Germany") == "https://flagcdn.com/w40/de.png"
    # Germany emoji = regional indicators D+E
    assert flags.flag_emoji("Germany") == "\U0001F1E9\U0001F1EA"
    # home nations use tag-sequence emoji (non-empty, not the 2-letter form)
    assert flags.flag_emoji("England") and flags.flag_emoji("Scotland")
    assert flags.code_of("England") == "gb-eng"


def test_locks_disabled():
    """With ENFORCE_LOCKS off, nothing locks regardless of kickoff times."""
    from worldcup.locks import compute_locks
    past = [{"round": "GROUP", "grp_code": "A", "kickoff_at": "2020-01-01T00:00:00Z"},
            {"round": "R32", "grp_code": None, "kickoff_at": "2020-01-01T00:00:00Z"}]
    locks = compute_locks(past)
    assert locks["groups"]["A"] is False
    assert locks["rounds"]["R32"] is False
