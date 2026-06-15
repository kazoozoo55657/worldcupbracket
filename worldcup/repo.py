"""Data-access helpers: load tournament data + member picks, save picks, leaderboard."""
from __future__ import annotations

import sqlite3

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
    out: dict[str, set[int]] = {}
    for r in conn.execute(
        "SELECT grp_code, team_id FROM group_pick WHERE member_id = ?", (member_id,)
    ):
        out.setdefault(r["grp_code"], set()).add(r["team_id"])
    return out


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


# ---- pick saving (validated against locks + monotonicity) ----

class PickError(Exception):
    pass


def save_group_pick(conn, member_id: int, grp: str, team_ids: list[int], locks: dict):
    if locks["groups"].get(grp):
        raise PickError(f"Group {grp} is locked — its first match has kicked off.")
    if len(team_ids) > GROUP_QUALIFIERS_PER_GROUP:
        raise PickError(f"Pick at most {GROUP_QUALIFIERS_PER_GROUP} teams to advance.")
    valid = {t["id"] for t in teams_in_group(conn, grp)}
    for tid in team_ids:
        if tid not in valid:
            raise PickError("A picked team is not in this group.")
    conn.execute("DELETE FROM group_pick WHERE member_id = ? AND grp_code = ?", (member_id, grp))
    for tid in team_ids:
        conn.execute(
            "INSERT INTO group_pick (member_id, grp_code, team_id) VALUES (?, ?, ?)",
            (member_id, grp, tid),
        )
    conn.commit()


def save_adv_pick(conn, member_id: int, rnd: str, team_ids: list[int], locks: dict):
    if rnd not in LAYER_BY_ROUND:
        raise PickError("Unknown knockout round.")
    if locks["rounds"].get(rnd):
        raise PickError(f"{LAYER_BY_ROUND[rnd]['label']} is locked — that round has started.")
    size = LAYER_BY_ROUND[rnd]["size"]
    if len(team_ids) > size:
        raise PickError(f"Pick at most {size} teams for {LAYER_BY_ROUND[rnd]['label']}.")
    # Monotonicity: picks for a deeper round must be a subset of the previous round's picks.
    idx = KNOCKOUT_ROUNDS.index(rnd)
    if idx > 0:
        prev = KNOCKOUT_ROUNDS[idx - 1]
        prev_picks = member_adv_picks(conn, member_id).get(prev, set())
        for tid in team_ids:
            if tid not in prev_picks:
                raise PickError(
                    f"Each {LAYER_BY_ROUND[rnd]['label']} pick must first be picked in "
                    f"{LAYER_BY_ROUND[prev]['label']}."
                )
    conn.execute(
        "DELETE FROM advancement_pick WHERE member_id = ? AND round = ?", (member_id, rnd)
    )
    for tid in team_ids:
        conn.execute(
            "INSERT INTO advancement_pick (member_id, round, team_id) VALUES (?, ?, ?)",
            (member_id, rnd, tid),
        )
    # Cascade: drop any deeper-round picks that are no longer a subset of this round.
    picked = set(team_ids)
    for deeper in KNOCKOUT_ROUNDS[idx + 1:]:
        rows = conn.execute(
            "SELECT team_id FROM advancement_pick WHERE member_id = ? AND round = ?",
            (member_id, deeper),
        ).fetchall()
        for row in rows:
            if row["team_id"] not in picked:
                conn.execute(
                    "DELETE FROM advancement_pick WHERE member_id = ? AND round = ? AND team_id = ?",
                    (member_id, deeper, row["team_id"]),
                )
        picked = {r["team_id"] for r in conn.execute(
            "SELECT team_id FROM advancement_pick WHERE member_id = ? AND round = ?",
            (member_id, deeper)).fetchall()}
    conn.commit()
