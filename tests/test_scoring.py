"""Unit tests for the pure scoring engine (no DB required)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worldcup.scoring import TournamentState, score_member, compute_standings


def gm(grp, h, a, hs, as_, status="FINISHED"):
    return {"round": "GROUP", "grp_code": grp, "home_team_id": h, "away_team_id": a,
            "home_score": hs, "away_score": as_, "status": status, "winner_team_id": None,
            "kickoff_at": "2026-06-11T18:00:00Z"}


def km(rnd, h, a, winner, status="FINISHED", pens=False):
    return {"round": rnd, "grp_code": None, "home_team_id": h, "away_team_id": a,
            "home_score": None, "away_score": None, "status": status,
            "winner_team_id": winner, "went_to_pens": pens, "kickoff_at": "2026-06-20T18:00:00Z"}


def test_standings_order():
    # team 1 wins both, team 2 one win, team 3/4 lose.
    ms = [gm("A", 1, 2, 2, 0), gm("A", 3, 4, 0, 1), gm("A", 1, 3, 1, 0),
          gm("A", 2, 4, 3, 0), gm("A", 1, 4, 2, 0), gm("A", 2, 3, 1, 1)]
    order = compute_standings(ms)
    assert order[0] == 1
    assert set(order[:2]) == {1, 2}


def test_group_scoring_partial_and_full():
    ms = [gm("A", 1, 2, 2, 0), gm("A", 3, 4, 0, 1), gm("A", 1, 3, 1, 0),
          gm("A", 2, 4, 3, 0), gm("A", 1, 4, 2, 0), gm("A", 2, 3, 1, 1)]
    st = TournamentState.from_matches(ms)
    assert st.group_complete["A"] is True
    # picked both correct advancers -> 2 pts (1 each)
    s = score_member(st, 1, "x", {"A": {1, 2}}, {})
    assert s.group_earned == 2 and s.group_available == 0
    # one right, one wrong -> 1 pt
    s2 = score_member(st, 2, "y", {"A": {1, 4}}, {})
    assert s2.group_earned == 1


def test_group_available_until_final():
    ms = [gm("A", 1, 2, 2, 0, status="SCHEDULED")]  # not finished
    st = TournamentState.from_matches(ms)
    s = score_member(st, 1, "x", {"A": {1, 2}}, {})
    assert s.group_earned == 0
    assert s.group_available == 2  # optimistic: 2 picks * 1 pt


def test_knockout_earned_and_available():
    # R32: winners 1 and 3 advance; member picked 1 (right) and 9 (wrong-still scheduled? eliminated)
    ms = [km("R32", 1, 2, 1), km("R32", 3, 4, 3)]
    st = TournamentState.from_matches(ms)
    # member picked 1 and 3 to win R32 -> both reached -> 2 pts (1 each)
    s = score_member(st, 1, "x", {}, {"R32": {1, 3}})
    assert s.ko_earned == 2
    assert s.ko_available == 0
    # team 2 was eliminated
    assert not st.alive(2)
    assert st.alive(1)


def test_pens_winner_advances():
    ms = [km("SF", 5, 6, 6, pens=True)]
    st = TournamentState.from_matches(ms)
    assert 6 in st.reached["SF"]
    assert not st.alive(5)
    s = score_member(st, 1, "x", {}, {"SF": {6}})
    assert s.ko_earned == 8  # SF layer worth 8


def test_champion_pick_alive_contributes_to_all_remaining_layers():
    # Team 1 won its R32 match (reached R16). Still alive. Member made it champion.
    ms = [km("R32", 1, 2, 1)]
    st = TournamentState.from_matches(ms)
    picks = {"R32": {1}, "R16": {1}, "QF": {1}, "SF": {1}, "F": {1}}
    s = score_member(st, 1, "x", {}, picks)
    # earned: R32 layer (reached R16) = 1
    assert s.ko_earned == 1
    # available: R16(2)+QF(4)+SF(8)+F(16) = 30 (alive, not yet reached those)
    assert s.ko_available == 30
    assert s.ceiling == 31


def test_eliminated_champion_pick_drops_all_available():
    # Team 1 LOSES its R32 match -> eliminated. All deeper picks worth 0.
    ms = [km("R32", 1, 2, 2)]
    st = TournamentState.from_matches(ms)
    picks = {"R32": {1}, "R16": {1}, "QF": {1}, "SF": {1}, "F": {1}}
    s = score_member(st, 1, "x", {}, picks)
    assert s.ko_earned == 0
    assert s.ko_available == 0
    assert not st.alive(1)


def test_non_qualifier_eliminated_after_group_stage():
    # Full group stage done for group A; only teams 1,2 appear in an R32 match.
    gms = [gm("A", 1, 2, 1, 0), gm("A", 3, 4, 2, 1), gm("A", 1, 3, 1, 0),
           gm("A", 2, 4, 1, 0), gm("A", 1, 4, 1, 0), gm("A", 2, 3, 2, 0)]
    r32 = [km("R32", 1, 99, None, status="SCHEDULED"), km("R32", 2, 98, None, status="SCHEDULED")]
    st = TournamentState.from_matches(gms + r32)
    assert st.group_stage_complete and st.group_qualifiers_known
    assert not st.alive(3) and not st.alive(4)  # didn't make knockout
    assert st.alive(1) and st.alive(2)
