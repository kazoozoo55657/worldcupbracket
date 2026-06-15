"""Tests for the football-data.org v4 mapping + idempotent sync."""
import os
import sys
import tempfile

os.environ["WC_DB_PATH"] = tempfile.mktemp(suffix=".db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worldcup import db, footballdata  # noqa: E402

RAW = [
    {"id": 1001, "utcDate": "2026-06-12T19:00:00Z", "status": "FINISHED",
     "stage": "GROUP_STAGE", "group": "GROUP_A",
     "homeTeam": {"id": 759, "name": "Brazil", "tla": "BRA"},
     "awayTeam": {"id": 760, "name": "Serbia", "tla": "SRB"},
     "score": {"winner": "HOME_TEAM", "duration": "REGULAR", "fullTime": {"home": 2, "away": 0}}},
    {"id": 1002, "utcDate": "2026-06-12T16:00:00Z", "status": "SCHEDULED",
     "stage": "GROUP_STAGE", "group": "GROUP_A",
     "homeTeam": {"id": 761, "name": "Switzerland", "tla": "SUI"},
     "awayTeam": {"id": 762, "name": "Cameroon", "tla": "CMR"},
     "score": {"winner": None, "duration": "REGULAR", "fullTime": {"home": None, "away": None}}},
    {"id": 1003, "utcDate": "2026-06-13T19:00:00Z", "status": "FINISHED",
     "stage": "GROUP_STAGE", "group": "GROUP_B",
     "homeTeam": {"id": 770, "name": "England", "tla": "ENG"},
     "awayTeam": {"id": 771, "name": "Iran", "tla": "IRN"},
     "score": {"winner": "HOME_TEAM", "duration": "REGULAR", "fullTime": {"home": 6, "away": 2}}},
    # Knockout with a penalty shootout: fullTime level, AWAY_TEAM wins on pens.
    {"id": 2001, "utcDate": "2026-06-29T19:00:00Z", "status": "FINISHED",
     "stage": "LAST_32", "group": None,
     "homeTeam": {"id": 770, "name": "England", "tla": "ENG"},
     "awayTeam": {"id": 773, "name": "France", "tla": "FRA"},
     "score": {"winner": "AWAY_TEAM", "duration": "PENALTY_SHOOTOUT",
               "fullTime": {"home": 1, "away": 1}, "penalties": {"home": 3, "away": 4}}},
    # Not-yet-determined knockout fixture (teams TBD).
    {"id": 2002, "utcDate": "2026-06-29T22:00:00Z", "status": "TIMED",
     "stage": "LAST_32", "group": None,
     "homeTeam": {"id": None, "name": None, "tla": None},
     "awayTeam": {"id": None, "name": None, "tla": None},
     "score": {"winner": None, "duration": "REGULAR", "fullTime": {"home": None, "away": None}}},
    # Third-place playoff — we don't model it; must be skipped.
    {"id": 3001, "utcDate": "2026-07-18T19:00:00Z", "status": "SCHEDULED",
     "stage": "THIRD_PLACE", "group": None,
     "homeTeam": {"id": 759, "name": "Brazil", "tla": "BRA"},
     "awayTeam": {"id": 770, "name": "England", "tla": "ENG"},
     "score": {"winner": None, "duration": "REGULAR", "fullTime": {"home": None, "away": None}}},
]


def _fresh_conn():
    if os.path.exists(os.environ["WC_DB_PATH"]):
        os.remove(os.environ["WC_DB_PATH"])
    db.init_db()
    return db.connect()


def test_sync_maps_and_skips():
    conn = _fresh_conn()
    counts = footballdata.sync(conn, RAW)
    # 7 distinct teams across the modelled matches (third-place adds none new):
    # Brazil, Serbia, Switzerland, Cameroon (grp A); England, Iran (grp B); France (knockout).
    assert counts["teams"] == 7
    # 5 modelled matches (third-place skipped).
    assert counts["matches"] == 5
    assert conn.execute("SELECT COUNT(*) c FROM match WHERE round='F'").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM match").fetchone()["c"] == 5

    # Group derivation from group-stage matches.
    bra = conn.execute("SELECT grp, code FROM team WHERE ext_id='759'").fetchone()
    assert bra["grp"] == "A" and bra["code"] == "BRA"
    eng = conn.execute("SELECT grp FROM team WHERE ext_id='770'").fetchone()
    assert eng["grp"] == "B"

    # Status mapping + winner.
    m1 = conn.execute("SELECT * FROM match WHERE ext_id='1001'").fetchone()
    assert m1["status"] == "FINISHED" and m1["round"] == "GROUP" and m1["grp_code"] == "A"
    bra_id = conn.execute("SELECT id FROM team WHERE ext_id='759'").fetchone()["id"]
    assert m1["winner_team_id"] == bra_id

    # Penalty shootout: went_to_pens + winner is the AWAY team (France).
    pens = conn.execute("SELECT * FROM match WHERE ext_id='2001'").fetchone()
    fra_id = conn.execute("SELECT id FROM team WHERE ext_id='773'").fetchone()["id"]
    assert pens["went_to_pens"] == 1 and pens["winner_team_id"] == fra_id and pens["round"] == "R32"

    # TBD knockout fixture inserted with NULL teams.
    tbd = conn.execute("SELECT * FROM match WHERE ext_id='2002'").fetchone()
    assert tbd["home_team_id"] is None and tbd["away_team_id"] is None and tbd["round"] == "R32"
    conn.close()


def test_sync_is_idempotent_and_respects_admin_lock():
    conn = _fresh_conn()
    footballdata.sync(conn, RAW)
    before = conn.execute("SELECT COUNT(*) c FROM match").fetchone()["c"]

    # Admin locks the pens match with a different (manual) winner.
    conn.execute("UPDATE match SET result_locked=1, winner_team_id=NULL, result_source='admin' "
                 "WHERE ext_id='2001'")
    conn.commit()

    counts = footballdata.sync(conn, RAW)
    after = conn.execute("SELECT COUNT(*) c FROM match").fetchone()["c"]
    assert after == before  # no duplicate inserts
    assert counts["skipped_locked"] == 1
    # Admin's value preserved.
    locked = conn.execute("SELECT winner_team_id, result_source FROM match WHERE ext_id='2001'").fetchone()
    assert locked["winner_team_id"] is None and locked["result_source"] == "admin"
    conn.close()
