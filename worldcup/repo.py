"""Data-access helpers: load tournament data + member picks, save picks, leaderboard."""
from __future__ import annotations

import sqlite3

from . import bracket_structure
from .config import KNOCKOUT_ROUNDS, LAYER_BY_ROUND, GROUP_QUALIFIERS_PER_GROUP
from .scoring import TournamentState, leaderboard as _leaderboard, MemberScore


def all_matches(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM match").fetchall()]


def all_teams(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM team ORDER BY grp, name").fetchall()]


def teams_by_id(conn) -> dict[int, dict]:
    return {t["id"]: t for t in all_teams(conn)}


def all_groups(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM grp ORDER BY code").fetchall()]


def teams_in_group(conn, grp: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM team WHERE grp = ? ORDER BY name", (grp,)).fetchall()]


def member_group_picks(conn, member_id: int) -> dict[str, set[int]]:
    """Advancing-team sets per group, for scoring (rank-agnostic)."""
    out: dict[str, set[int]] = {}
    for r in conn.execute(
        "SELECT grp_code, team_id FROM group_pick WHERE member_id = ?", (member_id,)
    ):
        out.setdefault(r["grp_code"], set()).add(r["team_id"])
    return out


def member_group_ranked(conn, member_id: int) -> dict[str, dict]:
    """{grp: {'winner': team_id|None, 'runner': team_id|None}} for bracket filling."""
    by_grp: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT grp_code, team_id, rank FROM group_pick WHERE member_id = ?", (member_id,)
    ):
        by_grp.setdefault(r["grp_code"], {})[r["rank"]] = r["team_id"]
    return {g: {"winner": d.get(1), "runner": d.get(2)} for g, d in by_grp.items()}


def member_slot_picks(conn, member_id: int) -> dict[int, int]:
    return {r["match_no"]: r["team_id"] for r in conn.execute(
        "SELECT match_no, team_id FROM slot_pick WHERE member_id = ?", (member_id,))}


def member_adv_picks(conn, member_id: int) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {r: set() for r in KNOCKOUT_ROUNDS}
    for r in conn.execute(
        "SELECT round, team_id FROM advancement_pick WHERE member_id = ?", (member_id,)
    ):
        out.setdefault(r["round"], set()).add(r["team_id"])
    return out


def all_members(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM member WHERE is_admin = 0 ORDER BY bracket_name").fetchall()]


def get_member(conn, member_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM member WHERE id = ?", (member_id,)).fetchone()
    return dict(r) if r else None


def get_member_by_name(conn, name: str) -> dict | None:
    r = conn.execute("SELECT * FROM member WHERE bracket_name = ?", (name,)).fetchone()
    return dict(r) if r else None


def build_state(conn) -> TournamentState:
    return TournamentState.from_matches(all_matches(conn))


def leaderboard(conn) -> list[MemberScore]:
    state = build_state(conn)
    members = all_members(conn)
    gp = {m["id"]: member_group_picks(conn, m["id"]) for m in members}
    ap = {m["id"]: member_adv_picks(conn, m["id"]) for m in members}
    return _leaderboard(state, members, gp, ap)


# ---- bracket resolution + pick saving ----

class PickError(Exception):
    pass


def member_fillers(conn, member_id):
    """The member's predicted R32 fillers: (group_winner, group_runner, slot)."""
    ranked = member_group_ranked(conn, member_id)
    gw = {g: d["winner"] for g, d in ranked.items() if d.get("winner")}
    gr = {g: d["runner"] for g, d in ranked.items() if d.get("runner")}
    return gw, gr, member_slot_picks(conn, member_id)


def resolve_member(conn, member_id: int):
    """(participants, winners) per knockout match, driven by the member's group picks."""
    gw, gr, slot = member_fillers(conn, member_id)
    rounds = member_adv_picks(conn, member_id)
    return bracket_structure.resolve(gw, gr, slot, rounds)


def set_group_pick(conn, member_id, grp, winner_id, runner_id, locks):
    """Set a group's predicted winner (rank 1) and runner-up (rank 2). None clears."""
    if locks["groups"].get(grp):
        raise PickError(f"Group {grp} is locked — its first match has kicked off.")
    valid = {t["id"] for t in teams_in_group(conn, grp)}
    for tid in (winner_id, runner_id):
        if tid and tid not in valid:
            raise PickError("A picked team is not in this group.")
    if winner_id and winner_id == runner_id:
        raise PickError("Winner and runner-up must be different teams.")
    conn.execute("DELETE FROM group_pick WHERE member_id=? AND grp_code=?", (member_id, grp))
    for rank, tid in ((1, winner_id), (2, runner_id)):
        if tid:
            conn.execute(
                "INSERT INTO group_pick (member_id, grp_code, team_id, rank) VALUES (?,?,?,?)",
                (member_id, grp, tid, rank),
            )
    conn.commit()


def set_slot_pick(conn, member_id, match_no, team_id):
    """Set the predicted team for a third-place R32 slot (None clears)."""
    if match_no not in bracket_structure.THIRD_PLACE_SLOTS:
        raise PickError("Not a third-place slot.")
    conn.execute("DELETE FROM slot_pick WHERE member_id=? AND match_no=?", (member_id, match_no))
    if team_id:
        conn.execute(
            "INSERT INTO slot_pick (member_id, match_no, team_id) VALUES (?,?,?)",
            (member_id, match_no, team_id),
        )
    conn.commit()


def set_round_winners(conn, member_id, round_winners: dict[str, set]):
    """Replace all advancement (match-winner) picks with the given clean sets."""
    conn.execute("DELETE FROM advancement_pick WHERE member_id=?", (member_id,))
    for rnd, teams in round_winners.items():
        for tid in teams:
            conn.execute(
                "INSERT INTO advancement_pick (member_id, round, team_id) VALUES (?,?,?)",
                (member_id, rnd, tid),
            )
    conn.commit()
