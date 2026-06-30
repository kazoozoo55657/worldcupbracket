"""football-data.org v4 integration: idempotent upsert of the World Cup competition.

A single sync() handles both seeding and polling. It derives the full team list and
group assignments from the /matches payload itself (every match embeds homeTeam/
awayTeam objects with id/name/tla), so no separate /teams call is needed.

Match rows are keyed by the feed's match id (match.ext_id). Re-running sync updates
existing rows and inserts newly-published fixtures (e.g. knockout pairings that appear
once the draw is set) — but never overwrites a row the admin has locked.

Field shapes (v4):
  match: id, utcDate, status, stage, group, homeTeam{id,name,tla}, awayTeam{...},
         score{winner: DRAW|HOME_TEAM|AWAY_TEAM, duration: REGULAR|EXTRA_TIME|
         PENALTY_SHOOTOUT, fullTime{home,away}, penalties{home,away}}
"""
from __future__ import annotations

from .config import config, now_utc, iso, parse_iso

COMPETITION = "WC"  # FIFA World Cup
API_URL = f"https://api.football-data.org/v4/competitions/{COMPETITION}/matches"

# football-data stage -> our round code. Stages we don't model (third-place playoff,
# qualification, etc.) map to None and are skipped.
STAGE_TO_ROUND = {
    "GROUP_STAGE": "GROUP",
    "LAST_32": "R32",
    "LAST_16": "R16",
    "QUARTER_FINALS": "QF",
    "SEMI_FINALS": "SF",
    "FINAL": "F",
}

_LIVE = {"IN_PLAY", "PAUSED"}
_DONE = {"FINISHED", "AWARDED"}


def map_status(fd_status: str) -> str:
    if fd_status in _DONE:
        return "FINISHED"
    if fd_status in _LIVE:
        return "LIVE"
    return "SCHEDULED"  # SCHEDULED, TIMED, POSTPONED, SUSPENDED, CANCELLED


def strip_group(group: str | None) -> str | None:
    if not group:
        return None
    return group.replace("GROUP_", "").strip() or None


def _team_obj(side: dict | None) -> dict | None:
    """Return {ext_id, name, tla} for a side, or None if the team is undetermined."""
    if not side:
        return None
    tid = side.get("id")
    if tid is None:
        return None
    return {"ext_id": str(tid), "name": side.get("name") or f"Team {tid}",
            "tla": side.get("tla")}


def fetch_raw(timeout: int = 30) -> list[dict]:
    """Fetch all World Cup matches from football-data.org. Requires an API key."""
    import httpx

    if not config.FOOTBALLDATA_API_KEY:
        raise RuntimeError("FOOTBALLDATA_API_KEY not set")
    resp = httpx.get(API_URL, headers={"X-Auth-Token": config.FOOTBALLDATA_API_KEY},
                     timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("matches", [])


def sync(conn, raw_matches: list[dict], source: str = "api:football-data") -> dict:
    """Upsert groups, teams and matches from raw football-data match objects.

    Returns counts {'teams', 'matches', 'skipped_locked'}.
    """
    # ---- pass 1: collect teams + group assignments from the payload ----
    teams: dict[str, dict] = {}          # ext_id -> {name, tla, grp}
    groups: set[str] = set()
    for m in raw_matches:
        rnd = STAGE_TO_ROUND.get(m.get("stage"))
        if rnd is None:
            continue
        grp = strip_group(m.get("group")) if rnd == "GROUP" else None
        if grp:
            groups.add(grp)
        for side in (_team_obj(m.get("homeTeam")), _team_obj(m.get("awayTeam"))):
            if not side:
                continue
            rec = teams.setdefault(side["ext_id"], {"name": side["name"], "tla": side["tla"], "grp": None})
            rec["name"] = side["name"] or rec["name"]
            if side["tla"]:
                rec["tla"] = side["tla"]
            if grp:
                rec["grp"] = grp

    # ---- upsert groups ----
    for g in sorted(groups):
        conn.execute(
            "INSERT INTO grp (code, name) VALUES (?, ?) ON CONFLICT(code) DO NOTHING",
            (g, f"Group {g}"),
        )

    # ---- upsert teams, build ext_id -> local id map ----
    ext_to_id: dict[str, int] = {}
    for ext_id, t in teams.items():
        conn.execute(
            "INSERT INTO team (name, code, ext_id, grp) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(ext_id) DO UPDATE SET name=excluded.name, "
            "code=COALESCE(excluded.code, team.code), grp=COALESCE(excluded.grp, team.grp)",
            (t["name"], (t["tla"] or None), ext_id, t["grp"]),
        )
    for ext_id in teams:
        row = conn.execute("SELECT id FROM team WHERE ext_id = ?", (ext_id,)).fetchone()
        if row:
            ext_to_id[ext_id] = row["id"]

    # ---- upsert matches ----
    # Seed slot counters from existing rows so newly-inserted fixtures get stable labels.
    seq: dict[str, int] = {}
    for r in conn.execute("SELECT round, COUNT(*) n FROM match GROUP BY round"):
        seq[r["round"]] = r["n"]

    n_matches = 0
    n_skipped = 0
    ts = iso(now_utc())
    for m in raw_matches:
        rnd = STAGE_TO_ROUND.get(m.get("stage"))
        if rnd is None:
            continue
        ext_id = str(m.get("id"))
        score = m.get("score") or {}
        full = score.get("fullTime") or {}
        winner_code = score.get("winner")
        home = _team_obj(m.get("homeTeam"))
        away = _team_obj(m.get("awayTeam"))
        home_id = ext_to_id.get(home["ext_id"]) if home else None
        away_id = ext_to_id.get(away["ext_id"]) if away else None
        winner_id = None
        if winner_code == "HOME_TEAM":
            winner_id = home_id
        elif winner_code == "AWAY_TEAM":
            winner_id = away_id
        went_pens = 1 if score.get("duration") == "PENALTY_SHOOTOUT" else 0
        status = map_status(m.get("status"))
        # Fallback: football-data sometimes leaves score.winner null on penalty
        # shootouts (and occasionally other finished games) even though fullTime
        # already holds the decisive aggregate. A FINISHED match with a decisive
        # fullTime must have a winner, so derive it from the score — otherwise the
        # advancement scoring credits nobody and the next round can't resolve.
        if winner_id is None and status == "FINISHED" and home_id and away_id:
            fh, fa = full.get("home"), full.get("away")
            if fh is not None and fa is not None and fh != fa:
                winner_id = home_id if fh > fa else away_id
        grp = strip_group(m.get("group")) if rnd == "GROUP" else None
        kickoff = m.get("utcDate") or ts
        # normalize to our stored format
        try:
            kickoff = iso(parse_iso(kickoff))
        except Exception:
            kickoff = ts

        existing = conn.execute("SELECT id, result_locked FROM match WHERE ext_id = ?", (ext_id,)).fetchone()
        if existing:
            if existing["result_locked"]:
                n_skipped += 1
                continue
            conn.execute(
                "UPDATE match SET round=?, grp_code=?, home_team_id=?, away_team_id=?, "
                "kickoff_at=?, status=?, home_score=?, away_score=?, winner_team_id=?, "
                "went_to_pens=?, result_source=?, updated_at=? WHERE id=?",
                (rnd, grp, home_id, away_id, kickoff, status, full.get("home"),
                 full.get("away"), winner_id, went_pens, source, ts, existing["id"]),
            )
        else:
            seq[rnd] = seq.get(rnd, 0) + 1
            slot = "F" if rnd == "F" else f"{rnd}-{seq[rnd]}"
            conn.execute(
                "INSERT INTO match (ext_id, round, grp_code, slot, home_team_id, away_team_id, "
                "kickoff_at, status, home_score, away_score, winner_team_id, went_to_pens, "
                "result_source, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ext_id, rnd, grp, slot, home_id, away_id, kickoff, status,
                 full.get("home"), full.get("away"), winner_id, went_pens, source, ts),
            )
        n_matches += 1
    conn.commit()
    return {"teams": len(ext_to_id), "matches": n_matches, "skipped_locked": n_skipped}
