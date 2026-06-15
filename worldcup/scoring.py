"""Scoring engine and tournament-state derivation.

Everything here is pure: it takes plain match rows + member picks and returns
numbers, so it can be unit-tested without a database. The advancement model means
we never compare predicted *matchups* to real ones — we only ask, per round,
"did the team you picked win that round / advance out of it?".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import (
    KNOCKOUT_LAYERS,
    KNOCKOUT_ROUNDS,
    LAYER_BY_ROUND,
    GROUP_POINTS_PER_QUALIFIER,
    GROUP_QUALIFIERS_PER_GROUP,
    parse_iso,
)

FINISHED = "FINISHED"


def _d(row) -> dict:
    return dict(row) if not isinstance(row, dict) else row


def compute_standings(group_matches: list[dict]) -> list[int]:
    """Order the teams in one group by FIFA-ish rules from finished match scores.

    Tiebreak: points, goal difference, goals for, then lowest team id (a stable
    deterministic fallback). Real standings/tiebreakers can be corrected via the
    admin override of match scores; this is a reasonable local derivation.
    """
    stats: dict[int, dict] = {}

    def s(tid):
        return stats.setdefault(tid, {"pts": 0, "gf": 0, "ga": 0})

    for m in group_matches:
        if m.get("status") != FINISHED:
            continue
        h, a = m.get("home_team_id"), m.get("away_team_id")
        hs, as_ = m.get("home_score"), m.get("away_score")
        if h is None or a is None or hs is None or as_ is None:
            continue
        sh, sa = s(h), s(a)
        sh["gf"] += hs; sh["ga"] += as_
        sa["gf"] += as_; sa["ga"] += hs
        if hs > as_:
            sh["pts"] += 3
        elif as_ > hs:
            sa["pts"] += 3
        else:
            sh["pts"] += 1; sa["pts"] += 1

    return sorted(
        stats.keys(),
        key=lambda t: (-stats[t]["pts"], -(stats[t]["gf"] - stats[t]["ga"]), -stats[t]["gf"], t),
    )


@dataclass
class TournamentState:
    """Derived tournament facts, built once from all match rows."""
    matches: list[dict]
    reached: dict[str, set[int]] = field(default_factory=dict)        # round -> winners
    knockout_participants: set[int] = field(default_factory=set)       # teams in R32 matches
    eliminated: set[int] = field(default_factory=set)
    group_complete: dict[str, bool] = field(default_factory=dict)
    standings: dict[str, list[int]] = field(default_factory=dict)      # grp -> ordered team ids
    group_stage_complete: bool = False
    group_qualifiers_known: bool = False

    @classmethod
    def from_matches(cls, matches) -> "TournamentState":
        matches = [_d(m) for m in matches]
        st = cls(matches=matches)

        # Winners of each finished knockout round.
        for r in KNOCKOUT_ROUNDS:
            st.reached[r] = {
                m["winner_team_id"]
                for m in matches
                if m["round"] == r and m["status"] == FINISHED and m["winner_team_id"]
            }

        # Knockout qualifiers = teams appearing in R32 matches (once the feed sets them).
        for m in matches:
            if m["round"] == "R32":
                for tid in (m["home_team_id"], m["away_team_id"]):
                    if tid:
                        st.knockout_participants.add(tid)

        # Eliminations from knockout losses.
        for m in matches:
            if m["round"] in KNOCKOUT_ROUNDS and m["status"] == FINISHED and m["winner_team_id"]:
                for tid in (m["home_team_id"], m["away_team_id"]):
                    if tid and tid != m["winner_team_id"]:
                        st.eliminated.add(tid)

        # Group completeness + standings.
        groups = sorted({m["grp_code"] for m in matches if m["round"] == "GROUP" and m["grp_code"]})
        all_groups_done = bool(groups)
        for g in groups:
            gms = [m for m in matches if m["round"] == "GROUP" and m["grp_code"] == g]
            done = bool(gms) and all(m["status"] == FINISHED for m in gms)
            st.group_complete[g] = done
            if done:
                st.standings[g] = compute_standings(gms)
            else:
                all_groups_done = False
        st.group_stage_complete = all_groups_done
        st.group_qualifiers_known = all_groups_done and len(st.knockout_participants) > 0

        # Teams that played the group stage but did not qualify are eliminated.
        if st.group_qualifiers_known:
            group_teams = {
                tid
                for m in matches
                if m["round"] == "GROUP"
                for tid in (m["home_team_id"], m["away_team_id"])
                if tid
            }
            for tid in group_teams:
                if tid not in st.knockout_participants:
                    st.eliminated.add(tid)

        return st

    def alive(self, team_id: int) -> bool:
        return team_id not in self.eliminated

    def group_top2(self, grp: str) -> set[int]:
        if not self.group_complete.get(grp):
            return set()
        return set(self.standings.get(grp, [])[:GROUP_QUALIFIERS_PER_GROUP])


@dataclass
class MemberScore:
    member_id: int
    bracket_name: str
    group_earned: int = 0
    group_available: int = 0
    ko_earned: int = 0
    ko_available: int = 0
    joined_at: str = ""

    @property
    def total(self) -> int:
        return self.group_earned + self.ko_earned

    @property
    def available(self) -> int:
        return self.group_available + self.ko_available

    @property
    def ceiling(self) -> int:
        return self.total + self.available


def score_member(
    state: TournamentState,
    member_id: int,
    bracket_name: str,
    group_picks: dict[str, set[int]],      # grp -> team ids picked to advance
    adv_picks: dict[str, set[int]],        # round -> team ids picked to win that round
    joined_at: str = "",
) -> MemberScore:
    ms = MemberScore(member_id=member_id, bracket_name=bracket_name, joined_at=joined_at)

    # Group stage.
    all_groups = set(state.group_complete.keys()) | set(group_picks.keys())
    for g in all_groups:
        picks = group_picks.get(g, set())
        if state.group_complete.get(g):
            top2 = state.group_top2(g)
            ms.group_earned += GROUP_POINTS_PER_QUALIFIER * len(picks & top2)
        else:
            # Optimistic: an unfinished group still offers full credit for picks made.
            ms.group_available += GROUP_POINTS_PER_QUALIFIER * len(picks)

    # Knockout (advancement model).
    for layer in KNOCKOUT_LAYERS:
        r, val = layer["round"], layer["value"]
        picks = adv_picks.get(r, set())
        reached = state.reached.get(r, set())
        ms.ko_earned += val * len(picks & reached)
        still = {t for t in picks if state.alive(t) and t not in reached}
        ms.ko_available += val * len(still)

    return ms


def leaderboard(
    state: TournamentState,
    members: list[dict],
    group_picks_by_member: dict[int, dict[str, set[int]]],
    adv_picks_by_member: dict[int, dict[str, set[int]]],
) -> list[MemberScore]:
    scores = [
        score_member(
            state,
            m["id"],
            m["bracket_name"],
            group_picks_by_member.get(m["id"], {}),
            adv_picks_by_member.get(m["id"], {}),
            m.get("joined_at", ""),
        )
        for m in members
    ]
    # Tiebreak: total, then available, then knockout earned, then earliest joiner, then name.
    scores.sort(
        key=lambda s: (-s.total, -s.available, -s.ko_earned, s.joined_at, s.bracket_name.lower())
    )
    return scores
