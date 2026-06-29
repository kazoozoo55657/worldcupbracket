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


def test_owner_name_register_rename_and_delete():
    import tempfile
    import pytest
    from worldcup.config import config as cfg
    from worldcup import db as dbm, auth, repo

    saved = cfg.DB_PATH
    cfg.DB_PATH = tempfile.mktemp(suffix=".db")
    try:
        dbm.init_db()
        conn = dbm.connect()
        m = auth.register(conn, "Goal Diggers", "1234", "Cole")
        assert repo.get_member(conn, m["id"])["owner_name"] == "Cole"
        # owner_name flows to the leaderboard
        lb = repo.leaderboard(conn)
        assert any(s.bracket_name == "Goal Diggers" and s.owner_name == "Cole" for s in lb)
        # register requires an owner name
        with pytest.raises(ValueError):
            auth.register(conn, "No Owner", "1234", "")
        # rename bracket + owner
        auth.update_account(conn, m["id"], "Net Busters", "Cole K")
        row = repo.get_member(conn, m["id"])
        assert row["bracket_name"] == "Net Busters" and row["owner_name"] == "Cole K"
        # can't rename onto another member's bracket name
        auth.register(conn, "Other FC", "1234", "Bob")
        with pytest.raises(ValueError):
            auth.update_account(conn, m["id"], "Other FC", "Cole K")
        # self-delete removes the member
        conn.execute("DELETE FROM member WHERE id = ? AND is_admin = 0", (m["id"],))
        conn.commit()
        assert repo.get_member(conn, m["id"]) is None
        conn.close()
    finally:
        cfg.DB_PATH = saved


def test_admin_reset_pin():
    """Admin can set a new PIN for a member who forgot theirs: the old PIN stops
    working, the new one logs in, and any lockout is cleared."""
    import tempfile
    import pytest
    from worldcup.config import config as cfg
    from worldcup import db as dbm, auth, repo

    saved = cfg.DB_PATH
    cfg.DB_PATH = tempfile.mktemp(suffix=".db")
    try:
        dbm.init_db()
        conn = dbm.connect()
        m = auth.register(conn, "Forgot FC", "1234", "Pat")
        # Simulate a prior lockout from too many bad attempts.
        conn.execute("UPDATE member SET failed_logins = 9, lockout_until = '2999-01-01T00:00:00Z' "
                     "WHERE id = ?", (m["id"],))
        conn.commit()

        returned = auth.reset_pin(conn, m["id"], "5678")
        assert returned == "5678"
        # reset clears the lockout immediately
        row = repo.get_member(conn, m["id"])
        assert row["failed_logins"] == 0 and row["lockout_until"] is None
        # old PIN rejected, new PIN accepted
        with pytest.raises(ValueError):
            auth.login(conn, "Forgot FC", "1234")
        assert auth.login(conn, "Forgot FC", "5678")["id"] == m["id"]

        # auto-generated PINs are valid 4-digit strings
        gen = auth.generate_pin()
        assert gen.isdigit() and len(gen) == 4 and auth.valid_pin(gen)

        # bad PIN is rejected
        with pytest.raises(ValueError):
            auth.reset_pin(conn, m["id"], "12")
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


def _finish_group_lower_id_wins(conn, grp):
    """Play every match in a group 1-0 to the lower team id (deterministic standings)."""
    for m in conn.execute(
        "SELECT id, home_team_id, away_team_id FROM match WHERE grp_code=?", (grp,)
    ).fetchall():
        w = min(m["home_team_id"], m["away_team_id"])
        hs, as_ = (1, 0) if m["home_team_id"] < m["away_team_id"] else (0, 1)
        conn.execute("UPDATE match SET status='FINISHED', home_score=?, away_score=?, "
                     "winner_team_id=? WHERE id=?", (hs, as_, w, m["id"]))


def test_real_knockout_status_locks_by_official_number():
    """Knockout games are pinned to official match numbers and lock by number once the
    real fixture kicks off — including deeper rounds, regardless of any member's picks."""
    import tempfile
    from worldcup.config import config as cfg
    from worldcup import db as dbm, seed_data, repo
    from worldcup.scoring import real_knockout_status

    saved = cfg.DB_PATH
    cfg.DB_PATH = tempfile.mktemp(suffix=".db")
    try:
        dbm.init_db()
        conn = dbm.connect()
        seed_data.build_synthetic(conn)
        for g in [chr(ord("A") + i) for i in range(12)]:
            _finish_group_lower_id_wins(conn, g)  # standings = teams in ascending id
        tid = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM team")}
        e1, i1, t_a3, t_b3 = tid["E1"], tid["I1"], tid["A3"], tid["B3"]
        r32 = [r["id"] for r in conn.execute("SELECT id FROM match WHERE round='R32' ORDER BY id")]
        # Match 74 = winner(E) vs a 3rd; match 77 = winner(I) vs a 3rd. Both played.
        conn.execute("UPDATE match SET home_team_id=?, away_team_id=?, status='FINISHED', "
                     "winner_team_id=? WHERE id=?", (e1, t_a3, e1, r32[0]))
        conn.execute("UPDATE match SET home_team_id=?, away_team_id=?, status='FINISHED', "
                     "winner_team_id=? WHERE id=?", (i1, t_b3, i1, r32[1]))
        # The real R16 match 89 (winners of 74 & 77) is now underway.
        r16 = conn.execute("SELECT id FROM match WHERE round='R16' ORDER BY id LIMIT 1").fetchone()["id"]
        conn.execute("UPDATE match SET home_team_id=?, away_team_id=?, status='LIVE' WHERE id=?",
                     (e1, i1, r16))
        conn.commit()

        status = real_knockout_status(repo.all_matches(conn))
        assert status[74]["status"] == "FINISHED" and status[74]["winner_id"] == e1
        assert status[77]["status"] == "FINISHED" and status[77]["winner_id"] == i1
        assert status[89]["status"] == "LIVE"   # deeper game locks by number, even mid-match
        assert 73 not in status                 # its fixture wasn't set -> not lockable yet
        conn.close()
    finally:
        cfg.DB_PATH = saved


def test_knockout_game_locks_and_save_ignores_changes():
    """A played R32 game shows as locked, and POSTing a new winner for it is ignored."""
    import re
    import tempfile
    from fastapi.testclient import TestClient
    from worldcup.config import config as cfg
    from worldcup import db as dbm, repo, seed_data, auth, app as appmod

    saved = cfg.DB_PATH
    cfg.DB_PATH = tempfile.mktemp(suffix=".db")
    try:
        dbm.init_db()
        conn = dbm.connect()
        seed_data.build_synthetic(conn)
        # Finish groups A and B so match 73's participants (2A vs 2B) are both real.
        _finish_group_lower_id_wins(conn, "A")
        _finish_group_lower_id_wins(conn, "B")
        ids = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM team WHERE grp IN ('A','B')")}
        a2, b2 = ids["A2"], ids["B2"]   # runners-up = 2nd-lowest id in each group
        # The real R32 fixture between 2A and 2B has been played; 2A won.
        r32 = conn.execute("SELECT id FROM match WHERE round='R32' ORDER BY id LIMIT 1").fetchone()["id"]
        conn.execute("UPDATE match SET home_team_id=?, away_team_id=?, status='FINISHED', "
                     "winner_team_id=? WHERE id=?", (a2, b2, a2, r32))
        # A member who picked 2A to win that game (match 73).
        conn.execute("INSERT INTO member (bracket_name, pin_hash, is_admin, created_at, joined_at) "
                     "VALUES ('locktest', 'x', 0, 't', 't')")
        mid = conn.execute("SELECT id FROM member WHERE bracket_name='locktest'").fetchone()["id"]
        conn.execute("INSERT INTO advancement_pick (member_id, round, team_id) VALUES (?, 'R32', ?)", (mid, a2))
        conn.commit()

        # The bracket view marks match 73 locked, with 2A already its winner.
        view = appmod._build_bracket_view(conn, repo.get_member(conn, mid))
        assert view["ko_data"]["locks"][73] is True
        assert view["ko_data"]["choices"][73] == a2

        with TestClient(appmod.app) as client:
            client.cookies.set(auth.COOKIE_NAME, auth.make_session(mid, False))
            page = client.get("/bracket")
            token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
            # Attempt to flip the played game's winner to 2B — must be ignored.
            client.post("/bracket", data={"csrf_token": token, "win_73": str(b2)})

        r32_picks = {r["team_id"] for r in conn.execute(
            "SELECT team_id FROM advancement_pick WHERE member_id=? AND round='R32'", (mid,))}
        assert a2 in r32_picks and b2 not in r32_picks
        conn.close()
    finally:
        cfg.DB_PATH = saved


def test_wrong_pick_slot_shows_actual_winner_over_struck_pick():
    """When a member's predicted advancer didn't really advance, the next match's slot
    surfaces the team that actually won (`actual`) above the struck-out pick."""
    import tempfile
    from worldcup.config import config as cfg
    from worldcup import db as dbm, seed_data, repo, app as appmod

    saved = cfg.DB_PATH
    cfg.DB_PATH = tempfile.mktemp(suffix=".db")
    try:
        dbm.init_db()
        conn = dbm.connect()
        seed_data.build_synthetic(conn)
        for g in [chr(ord("A") + i) for i in range(12)]:
            _finish_group_lower_id_wins(conn, g)
        tid = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM team")}
        e1, a3 = tid["E1"], tid["A3"]
        r32 = [r["id"] for r in conn.execute("SELECT id FROM match WHERE round='R32' ORDER BY id")]
        # Real result of match 74: E1 beat A3 (winner E1). It feeds R16 match 89's home slot.
        conn.execute("UPDATE match SET home_team_id=?, away_team_id=?, status='FINISHED', "
                     "winner_team_id=? WHERE id=?", (e1, a3, e1, r32[0]))
        # Member wrongly picked A3 to win that R32 game, so A3 cascades into match 89.
        conn.execute("INSERT INTO member (bracket_name, pin_hash, is_admin, created_at, joined_at) "
                     "VALUES ('wrongpick', 'x', 0, 't', 't')")
        mid = conn.execute("SELECT id FROM member WHERE bracket_name='wrongpick'").fetchone()["id"]
        conn.execute("INSERT INTO advancement_pick (member_id, round, team_id) VALUES (?, 'R32', ?)", (mid, a3))
        conn.commit()

        view = appmod._build_bracket_view(conn, repo.get_member(conn, mid))
        assert view["ko_data"]["real_winners"].get(74) == e1
        cols = view["ko_left"] + view["ko_right"]
        m89 = next(m for c in cols for m in c["matches"] if m["no"] == 89)
        home = m89["home"]
        assert home["team"]["id"] == a3          # the (wrong) predicted occupant
        assert home["actual"] and home["actual"]["id"] == e1   # real advancer shown above it
        # A correct slot carries no `actual` marker.
        assert m89["away"]["actual"] is None
        conn.close()
    finally:
        cfg.DB_PATH = saved
