"""Seed the tournament structure into the DB.

Two sources:
  * build_synthetic()  - a deterministic fake 48-team tournament for local dev/tests.
  * seed_from_openfootball() - pull real 2026 teams/groups/fixtures (public-domain JSON).

Knockout matches are created as placeholders (NULL teams) up front; the poller fills
participants + winners as the real results feed reports them.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from .config import iso, parse_iso, KNOCKOUT_LAYERS
from .db import connect, init_db

GROUP_CODES = [chr(ord("A") + i) for i in range(12)]  # A..L

# Synthetic kickoff base. Real WC 2026 starts 2026-06-11; override with WC_SEED_BASE
# (ISO date) to generate a tournament whose matches are all in the future for testing.
BASE = parse_iso(os.environ.get("WC_SEED_BASE", "2026-06-11T18:00:00Z"))

# 4-team single round-robin pairings (index into the group's team list).
_RR = [(0, 1), (2, 3), (0, 2), (1, 3), (0, 3), (1, 2)]


def _clear(conn):
    for t in ("advancement_pick", "group_pick", "match", "team", "grp"):
        conn.execute(f"DELETE FROM {t}")


def _seed_groups_and_teams(conn, team_names: dict[str, list[str]]):
    """team_names: {group_code: [4 team names]}."""
    for g in GROUP_CODES:
        conn.execute("INSERT INTO grp (code, name) VALUES (?, ?)", (g, f"Group {g}"))
    for g, names in team_names.items():
        for name in names:
            conn.execute(
                "INSERT INTO team (name, code, grp) VALUES (?, ?, ?)",
                (name, name[:3].upper(), g),
            )


def _seed_group_matches(conn):
    day = 0
    for g in GROUP_CODES:
        team_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM team WHERE grp = ? ORDER BY id", (g,))]
        for n, (i, j) in enumerate(_RR):
            ko = BASE + timedelta(days=day, hours=(n % 3) * 3)
            conn.execute(
                "INSERT INTO match (round, grp_code, slot, home_team_id, away_team_id, "
                "kickoff_at, status) VALUES ('GROUP', ?, ?, ?, ?, ?, 'SCHEDULED')",
                (g, f"G{g}-{n+1}", team_ids[i], team_ids[j], iso(ko)),
            )
        day += 1


def _seed_knockout_placeholders(conn):
    # Knockout starts ~day 18; each round a few days apart.
    start_day = {"R32": 18, "R16": 22, "QF": 26, "SF": 29, "F": 32}
    for layer in KNOCKOUT_LAYERS:
        r = layer["round"]
        n_matches = layer["size"]  # winners == matches in that round
        for i in range(n_matches):
            ko = BASE + timedelta(days=start_day[r], hours=i % 2 * 4)
            slot = "F" if r == "F" else f"{r}-{i+1}"
            conn.execute(
                "INSERT INTO match (round, slot, kickoff_at, status) "
                "VALUES (?, ?, ?, 'SCHEDULED')",
                (r, slot, iso(ko)),
            )


def build_synthetic(conn=None) -> None:
    """Create a deterministic 48-team tournament with generic team names."""
    own = conn is None
    conn = conn or connect()
    try:
        _clear(conn)
        names = {g: [f"{g}{i+1}" for i in range(4)] for g in GROUP_CODES}
        _seed_groups_and_teams(conn, names)
        _seed_group_matches(conn)
        _seed_knockout_placeholders(conn)
        conn.commit()
    finally:
        if own:
            conn.close()


def seed_from_openfootball(conn=None) -> bool:
    """Seed real 2026 structure from openfootball/worldcup.json. Returns success.

    The public-domain repo publishes the schedule under a path like
    cup/2026--north-america/cup.txt (text) and JSON mirrors. Formats can shift, so
    this is best-effort: on any failure we return False and the caller falls back to
    synthetic seeding (structure stays correct; teams reconcile later via the poller).
    """
    import httpx

    candidates = [
        "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json",
        "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/cup.json",
    ]
    data = None
    for url in candidates:
        try:
            resp = httpx.get(url, timeout=20, follow_redirects=True)
            if resp.status_code == 200:
                data = resp.json()
                break
        except Exception:
            continue
    if not data:
        return False
    # NOTE: mapping the openfootball schema into our tables is finalized against the
    # live 2026 dataset during deployment seeding. Until validated, prefer synthetic.
    return False
