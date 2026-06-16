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


def test_r32_decoupled_from_group_picks():
    """R32 participants come from real results, not anyone's group predictions."""
    import tempfile
    from worldcup.config import config as cfg
    from worldcup import db as dbm, repo, seed_data

    saved = cfg.DB_PATH
    cfg.DB_PATH = tempfile.mktemp(suffix=".db")
    try:
        dbm.init_db()
        conn = dbm.connect()
        seed_data.build_synthetic(conn)
        a = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM team WHERE grp='A'")}
        b = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM team WHERE grp='B'")}
        # Finish Group A: A1>A2>A3>A4 (lower id wins 1-0).
        for m in conn.execute("SELECT id,home_team_id,away_team_id FROM match WHERE grp_code='A'").fetchall():
            w = min(m["home_team_id"], m["away_team_id"])
            hs, as_ = (1, 0) if m["home_team_id"] < m["away_team_id"] else (0, 1)
            conn.execute("UPDATE match SET status='FINISHED', home_score=?, away_score=?, "
                         "winner_team_id=? WHERE id=?", (hs, as_, w, m["id"]))
        # real R32 matchup populated: actual 1A (A1) vs B3 -> third slot 79 filler
        r32 = conn.execute("SELECT id FROM match WHERE round='R32' ORDER BY id LIMIT 1").fetchone()["id"]
        conn.execute("UPDATE match SET home_team_id=?, away_team_id=? WHERE id=?", (a["A1"], b["B3"], r32))
        conn.commit()

        agw, agr, aslot = repo.actual_r32_fillers(conn)
        assert agw["A"] == a["A1"] and agr["A"] == a["A2"]
        assert aslot.get(79) == b["B3"]

        # A member with NO group picks at all still sees the real R32 field.
        conn.execute("INSERT INTO member (bracket_name, pin_hash, is_admin, created_at, joined_at) "
                     "VALUES ('nopicks', 'x', 0, 't', 't')")
        mid = conn.execute("SELECT id FROM member WHERE bracket_name='nopicks'").fetchone()["id"]
        parts, _ = repo.resolve_member(conn, mid)
        assert parts[73] == (a["A2"], None)   # match 73 = 2A vs 2B; 2A known, 2B (group B unfinished) TBD
        assert parts[79][0] == a["A1"] and parts[79][1] == b["B3"]  # 1A vs the real 3rd
        conn.close()
    finally:
        cfg.DB_PATH = saved


def test_group_locks_only_when_complete():
    """A group locks once all its matches are FINISHED; knockout never locks here."""
    from worldcup.locks import compute_locks
    ms = [{"round": "GROUP", "grp_code": "A", "status": "FINISHED"},
          {"round": "GROUP", "grp_code": "A", "status": "FINISHED"},
          {"round": "GROUP", "grp_code": "B", "status": "FINISHED"},
          {"round": "GROUP", "grp_code": "B", "status": "SCHEDULED"},
          {"round": "R32", "grp_code": None, "status": "SCHEDULED"}]
    locks = compute_locks(ms)
    assert locks["groups"]["A"] is True     # complete -> locked
    assert locks["groups"]["B"] is False    # still has a game left -> open
    assert locks["rounds"]["R32"] is False  # knockout stays open
