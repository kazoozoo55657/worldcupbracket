"""When are picks frozen?

Group stage: a group locks once it is COMPLETE — every one of its matches has
finished (each team has played all 3 of its group games). Until then the group stays
open so people (incl. latecomers) can still pick while games remain to be played.
This stops anyone editing their original group picks once the group is decided.

Knockout: the bracket as a whole stays open so everyone can fill it in (a group's
picks auto-fill the Round of 32 the moment they're made). But each *individual*
knockout game locks by its official match number the instant the real fixture kicks
off — see ``scoring.real_knockout_status``. Once a game has started/finished nobody
can pick or change its winner, regardless of who they predicted would play in it, so
latecomers can't earn points on games that have already been decided.
"""
from __future__ import annotations

from datetime import datetime

from .config import KNOCKOUT_ROUNDS

FINISHED = "FINISHED"


def compute_locks(matches, now: datetime | None = None) -> dict:
    """Return {'groups': {grp: bool}, 'rounds': {round: bool}}.

    A group is locked when all of its group-stage matches are FINISHED.

    The per-round flags are kept for backward compatibility but are always False:
    the knockout locks *per game* by match number (see ``scoring.real_knockout_status``).
    """
    matches = [dict(m) for m in matches]
    groups = sorted({m["grp_code"] for m in matches if m["round"] == "GROUP" and m["grp_code"]})

    group_locked = {}
    for g in groups:
        gms = [m for m in matches if m["round"] == "GROUP" and m["grp_code"] == g]
        group_locked[g] = bool(gms) and all(m.get("status") == FINISHED for m in gms)

    round_locked = {r: False for r in KNOCKOUT_ROUNDS}
    return {"groups": group_locked, "rounds": round_locked}
