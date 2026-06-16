"""When are picks frozen?

Group stage: a group locks once it is COMPLETE — every one of its matches has
finished (each team has played all 3 of its group games). Until then the group stays
open so people (incl. latecomers) can still pick while games remain to be played.

Knockout: left open so everyone can fill the full bracket; a group's picks auto-fill
the Round of 32 the moment they're made.
"""
from __future__ import annotations

from datetime import datetime

from .config import KNOCKOUT_ROUNDS

FINISHED = "FINISHED"


def compute_locks(matches, now: datetime | None = None) -> dict:
    """Return {'groups': {grp: bool}, 'rounds': {round: bool}}.

    A group is locked when all of its group-stage matches are FINISHED.
    """
    matches = [dict(m) for m in matches]
    groups = sorted({m["grp_code"] for m in matches if m["round"] == "GROUP" and m["grp_code"]})

    group_locked = {}
    for g in groups:
        gms = [m for m in matches if m["round"] == "GROUP" and m["grp_code"] == g]
        group_locked[g] = bool(gms) and all(m.get("status") == FINISHED for m in gms)

    round_locked = {r: False for r in KNOCKOUT_ROUNDS}
    return {"groups": group_locked, "rounds": round_locked}
