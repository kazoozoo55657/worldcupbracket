"""SQLite connection management and one-time initialization."""
import os
import sqlite3
from pathlib import Path

from argon2 import PasswordHasher

from .config import config, now_utc, iso

_ph = PasswordHasher()
_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


def connect() -> sqlite3.Connection:
    """Open a connection with sane pragmas. Caller is responsible for closing."""
    db_path = config.DB_PATH
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 10000;")
    return conn


def init_db() -> None:
    """Create the schema (idempotent) and bootstrap the single pool row."""
    conn = connect()
    try:
        conn.executescript(_SCHEMA)
        # Lightweight migration for DBs created before the bracket rework:
        # add group_pick.rank if it's missing (slot_pick is created by the schema).
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(group_pick)")}
        if "rank" not in cols:
            conn.execute("ALTER TABLE group_pick ADD COLUMN rank INTEGER")
        row = conn.execute("SELECT id FROM pool WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO pool (id, name, join_code, admin_pin_hash, settings_json) "
                "VALUES (1, ?, ?, ?, '{}')",
                (config.POOL_NAME, config.JOIN_CODE, _ph.hash(config.ADMIN_PIN)),
            )
            # Seed an admin member so /admin is reachable out of the box.
            ts = iso(now_utc())
            conn.execute(
                "INSERT INTO member (bracket_name, pin_hash, is_admin, created_at, joined_at) "
                "VALUES (?, ?, 1, ?, ?)",
                ("admin", _ph.hash(config.ADMIN_PIN), ts, ts),
            )
        conn.commit()
    finally:
        conn.close()


def get_pool(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM pool WHERE id = 1").fetchone()
