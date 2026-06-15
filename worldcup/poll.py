"""CronJob entrypoint: seed the tournament and poll match results.

Usage:
    python -m worldcup.poll seed [--synthetic]
    python -m worldcup.poll poll [--source=SOURCE]

Sources:
    footballdata        live football-data.org /competitions/WC/matches (default)
    fdfile:PATH         a saved raw football-data response (for testing the mapping)
    file:PATH           a normalized [{match,key,winner,...}] list (simple manual feed)

Seeding and polling share one idempotent upsert (worldcup.footballdata.sync): it
derives teams + groups from the match payload, inserts newly-published fixtures, and
updates results — keyed by the feed's match id so reruns never duplicate rows.

Scoring is computed live on read (leaderboard()), so a successful poll simply needs to
land correct match results; there is no separate rescore step.

Result precedence: rows with result_locked=1 were set by the admin and are never
overwritten by the feed.
"""
from __future__ import annotations

import json
import sys

from .config import config, now_utc, iso
from .db import connect, init_db
from . import seed_data, footballdata


def log(msg: str) -> None:
    print(f"[{iso(now_utc())}] {msg}", flush=True)


# ---------- seeding ----------

def do_seed(synthetic: bool) -> None:
    init_db()
    conn = connect()
    try:
        if not synthetic:
            if config.FOOTBALLDATA_API_KEY:
                try:
                    counts = footballdata.sync(conn, footballdata.fetch_raw())
                    log(f"Seeded from football-data.org: {counts}")
                    return
                except Exception as e:  # noqa: BLE001
                    log(f"football-data seed failed ({e}) — trying openfootball.")
            if seed_data.seed_from_openfootball(conn):
                log("Seeded from openfootball.")
                return
            log("Live seed unavailable — falling back to synthetic structure.")
        seed_data.build_synthetic(conn)
        n = conn.execute("SELECT COUNT(*) c FROM match").fetchone()["c"]
        log(f"Seeded synthetic tournament: {n} matches, 48 teams.")
    finally:
        conn.close()


# ---------- result application ----------

def _resolve_team(conn, ident) -> int | None:
    if ident is None:
        return None
    row = conn.execute(
        "SELECT id FROM team WHERE ext_id = ? OR name = ? OR code = ?",
        (str(ident), str(ident), str(ident)),
    ).fetchone()
    return row["id"] if row else None


def _find_match(conn, rec: dict):
    key = rec.get("key", "ext_id")
    val = rec.get("match")
    col = "ext_id" if key == "ext_id" else "slot"
    return conn.execute(f"SELECT * FROM match WHERE {col} = ?", (val,)).fetchone()


def apply_records(conn, records: list[dict], source: str) -> int:
    """Upsert normalized result records. Returns number of matches changed."""
    changed = 0
    for rec in records:
        m = _find_match(conn, rec)
        if not m:
            continue
        if m["result_locked"]:
            continue  # admin override wins
        home_id = _resolve_team(conn, rec.get("home")) or m["home_team_id"]
        away_id = _resolve_team(conn, rec.get("away")) or m["away_team_id"]
        winner_id = _resolve_team(conn, rec.get("winner"))
        status = rec.get("status", m["status"])
        conn.execute(
            "UPDATE match SET home_team_id=?, away_team_id=?, home_score=?, away_score=?, "
            "winner_team_id=?, went_to_pens=?, status=?, result_source=?, updated_at=? WHERE id=?",
            (
                home_id, away_id,
                rec.get("home_score"), rec.get("away_score"),
                winner_id, 1 if rec.get("went_to_pens") else 0,
                status, source, iso(now_utc()), m["id"],
            ),
        )
        changed += 1
    conn.commit()
    return changed


# ---------- feeds ----------

def fetch_file(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def _raw_fd_from_file(path: str) -> list[dict]:
    """Load a raw football-data response ({"matches":[...]} or a bare list)."""
    with open(path) as f:
        data = json.load(f)
    return data.get("matches", []) if isinstance(data, dict) else data


def do_poll(source: str) -> None:
    init_db()
    conn = connect()
    try:
        if source == "footballdata":
            if not config.FOOTBALLDATA_API_KEY:
                log("No FOOTBALLDATA_API_KEY configured — nothing to poll; exiting cleanly.")
                return
            counts = footballdata.sync(conn, footballdata.fetch_raw())
            log(f"Poll via football-data.org: {counts}")
        elif source.startswith("fdfile:"):
            counts = footballdata.sync(conn, _raw_fd_from_file(source[len("fdfile:"):]), source="fdfile")
            log(f"Poll via raw football-data file: {counts}")
        elif source.startswith("file:"):
            changed = apply_records(conn, fetch_file(source[len("file:"):]), "file")
            log(f"Poll via normalized file: {changed} matches updated.")
        elif source == "openfootball":
            raise RuntimeError("openfootball live-results polling not implemented; use as seed/fallback")
        else:
            raise ValueError(f"unknown source: {source}")
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    mode = argv[0]
    args = argv[1:]
    if mode == "seed":
        do_seed(synthetic="--synthetic" in args)
        return 0
    if mode == "poll":
        source = "footballdata"
        for a in args:
            if a.startswith("--source"):
                source = a.split("=", 1)[1] if "=" in a else args[args.index(a) + 1]
        try:
            do_poll(source)
        except Exception as e:  # noqa: BLE001 - CronJob should log and fail visibly
            log(f"Poll failed: {e}")
            return 1
        return 0
    print(f"unknown mode: {mode}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
