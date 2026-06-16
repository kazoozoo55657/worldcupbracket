"""FIFA Men's World Ranking snapshot, used only to show 🥇🥈🥉 strength hints.

Snapshot date is shown in the UI. These ranks do not affect scoring — they just
mark each group's top 3 by FIFA rank. Update RANKS + RANKING_DATE after a new
ranking release if desired.
"""
from __future__ import annotations

import unicodedata

RANKING_DATE = "2026-06-11"

# Keyed by normalized country name (see _norm). Values are FIFA world ranks.
RANKS = {
    "argentina": 1, "spain": 2, "france": 3, "england": 4, "portugal": 5,
    "brazil": 6, "morocco": 7, "netherlands": 8, "belgium": 9, "germany": 10,
    "croatia": 11, "colombia": 13, "mexico": 14, "senegal": 15, "uruguay": 16,
    "united states": 17, "japan": 18, "switzerland": 19, "iran": 20, "turkey": 22,
    "ecuador": 23, "austria": 24, "south korea": 25, "australia": 27, "algeria": 28,
    "egypt": 29, "canada": 30, "norway": 31, "ivory coast": 33, "panama": 34,
    "sweden": 38, "czechia": 40, "paraguay": 41, "scotland": 42, "tunisia": 45,
    "congo dr": 46, "uzbekistan": 50, "qatar": 56, "iraq": 57, "south africa": 60,
    "saudi arabia": 61, "jordan": 63, "bosnia-herzegovina": 64,
    "cape verde islands": 67, "ghana": 73, "curacao": 82, "haiti": 83,
    "new zealand": 85,
}

_MEDALS = ["gold", "silver", "bronze"]
_MEDAL_ICON = {"gold": "🥇", "silver": "🥈", "bronze": "🥉"}


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def rank_of(name: str) -> int:
    return RANKS.get(_norm(name), 999)


def medal_icon(medal: str | None) -> str:
    return _MEDAL_ICON.get(medal, "")


def group_medals(teams: list[dict]) -> dict[int, str | None]:
    """Given a group's team rows ({id, name, ...}), return {team_id: 'gold'|'silver'|'bronze'|None}.

    Top 3 by FIFA rank (lowest rank number) get gold/silver/bronze; 4th gets None.
    """
    ordered = sorted(teams, key=lambda t: rank_of(t["name"]))
    out: dict[int, str | None] = {}
    for i, t in enumerate(ordered):
        out[t["id"]] = _MEDALS[i] if i < len(_MEDALS) else None
    return out
