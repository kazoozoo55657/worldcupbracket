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
        mcols = {r["name"] for r in conn.execute("PRAGMA table_info(member)")}
        if "owner_name" not in mcols:
            conn.execute("ALTER TABLE member ADD COLUMN owner_name TEXT")
        pcols = {r["name"] for r in conn.execute("PRAGMA table_info(pool)")}
        if "admin_failed_logins" not in pcols:
            conn.execute("ALTER TABLE pool ADD COLUMN admin_failed_logins INTEGER NOT NULL DEFAULT 0")
        if "admin_lockout_until" not in pcols:
            conn.execute("ALTER TABLE pool ADD COLUMN admin_lockout_until TEXT")
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
        else:
            _sync_admin_pin(conn)
        conn.commit()
    finally:
        conn.close()


def _sync_admin_pin(conn: sqlite3.Connection) -> None:
    """Make WC_ADMIN_PIN authoritative: the hash used to be written only when the DB
    was first created, so rotating the Secret silently did nothing. If the env PIN no
    longer matches, re-hash it into the pool row and the seeded 'admin' member."""
    from argon2.exceptions import VerifyMismatchError
    stored = conn.execute("SELECT admin_pin_hash FROM pool WHERE id = 1").fetchone()[0]
    try:
        _ph.verify(stored, config.ADMIN_PIN)
        return
    except VerifyMismatchError:
        pass
    new_hash = _ph.hash(config.ADMIN_PIN)
    conn.execute(
        "UPDATE pool SET admin_pin_hash = ?, admin_failed_logins = 0, admin_lockout_until = NULL "
        "WHERE id = 1", (new_hash,))
    conn.execute(
        "UPDATE member SET pin_hash = ?, failed_logins = 0, lockout_until = NULL "
        "WHERE bracket_name = 'admin' AND is_admin = 1", (_ph.hash(config.ADMIN_PIN),))


def get_pool(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM pool WHERE id = 1").fetchone()
