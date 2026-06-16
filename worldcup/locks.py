"""When are picks frozen? A unit locks at the kickoff of its first deciding match.

- A group's pick locks at that group's first kickoff.
- A knockout layer (R32/R16/QF/SF/F) locks at that round's first match kickoff.

A late joiner simply finds already-locked units read-only; they score 0 there.
"""
from __future__ import annotations

from datetime import datetime

from .config import KNOCKOUT_ROUNDS, ENFORCE_LOCKS, parse_iso, now_utc


def _earliest_kickoff(matches: list[dict], predicate) -> datetime | None:
    times = [parse_iso(m["kickoff_at"]) for m in matches if predicate(m) and m.get("kickoff_at")]
    return min(times) if times else None


def compute_locks(matches, now: datetime | None = None) -> dict:
    """Return {'groups': {grp: bool}, 'rounds': {round: bool}, 'lock_times': {...}}."""
    now = now or now_utc()
    matches = [dict(m) for m in matches]

    groups = sorted({m["grp_code"] for m in matches if m["round"] == "GROUP" and m["grp_code"]})
    group_locked, group_times = {}, {}
    for g in groups:
        t = _earliest_kickoff(matches, lambda m, g=g: m["round"] == "GROUP" and m["grp_code"] == g)
        group_times[g] = t
        group_locked[g] = ENFORCE_LOCKS and t is not None and now >= t

    round_locked, round_times = {}, {}
    for r in KNOCKOUT_ROUNDS:
        t = _earliest_kickoff(matches, lambda m, r=r: m["round"] == r)
        round_times[r] = t
        round_locked[r] = ENFORCE_LOCKS and t is not None and now >= t

    return {
        "groups": group_locked,
        "rounds": round_locked,
        "group_times": group_times,
        "round_times": round_times,
    }
