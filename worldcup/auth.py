"""Lightweight auth: join-code gate, bracket-name + PIN, signed session cookie."""
from __future__ import annotations

import hmac
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from .config import config, now_utc, iso, parse_iso
from .db import get_pool

_ph = PasswordHasher()
COOKIE_NAME = "wc_session"
NAME_RE = re.compile(r"^[A-Za-z0-9 _'\-]{2,40}$")
OWNER_RE = re.compile(r"^[A-Za-z0-9 _'\-.]{1,40}$")


def hash_pin(pin: str) -> str:
    return _ph.hash(pin)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.SESSION_SECRET, salt="wc-session")


def make_session(member_id: int, is_admin: bool) -> str:
    return _serializer().dumps({"m": member_id, "a": bool(is_admin)})


def read_session(token: str) -> dict | None:
    if not token:
        return None
    try:
        return _serializer().loads(token, max_age=config.SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def valid_bracket_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


def valid_owner_name(name: str) -> bool:
    return bool(OWNER_RE.match((name or "").strip()))


def valid_pin(pin: str) -> bool:
    return bool(pin) and pin.isdigit() and 4 <= len(pin) <= 6


def check_join_code(conn, code: str) -> bool:
    pool = get_pool(conn)
    return hmac.compare_digest((code or "").strip(), pool["join_code"])


def _is_locked_out(member: dict) -> bool:
    until = member.get("lockout_until")
    if not until:
        return False
    return now_utc() < parse_iso(until)


def register(conn, name: str, pin: str, owner_name: str = "") -> dict:
    """Create a member. Raises ValueError on bad input / duplicate name."""
    name = (name or "").strip()
    owner_name = (owner_name or "").strip()
    if not valid_bracket_name(name):
        raise ValueError("Bracket name must be 2–40 chars (letters, numbers, spaces, _ - ').")
    if not valid_owner_name(owner_name):
        raise ValueError("Enter your name (1–40 chars).")
    if not valid_pin(pin):
        raise ValueError("PIN must be 4–6 digits.")
    if conn.execute("SELECT 1 FROM member WHERE bracket_name = ?", (name,)).fetchone():
        raise ValueError("That bracket name is taken — pick another.")
    ts = iso(now_utc())
    cur = conn.execute(
        "INSERT INTO member (bracket_name, owner_name, pin_hash, is_admin, created_at, joined_at) "
        "VALUES (?, ?, ?, 0, ?, ?)",
        (name, owner_name, hash_pin(pin), ts, ts),
    )
    conn.commit()
    return {"id": cur.lastrowid, "bracket_name": name, "is_admin": 0}


def generate_pin() -> str:
    """A random 4-digit PIN (leading zeros allowed), for admin-driven resets."""
    return f"{secrets.randbelow(10000):04d}"


def reset_pin(conn, member_id: int, pin: str) -> str:
    """Admin sets a new PIN for a member (e.g. they forgot theirs). Validates,
    hashes, and clears any failed-login lockout. Returns the PIN that was set so
    the admin can relay it. Raises ValueError on bad PIN or unknown/admin member."""
    if not valid_pin(pin):
        raise ValueError("PIN must be 4–6 digits.")
    row = conn.execute("SELECT is_admin FROM member WHERE id = ?", (member_id,)).fetchone()
    if not row:
        raise ValueError("No such bracket.")
    if row["is_admin"]:
        raise ValueError("Use the pool config to change the admin PIN.")
    conn.execute(
        "UPDATE member SET pin_hash = ?, failed_logins = 0, lockout_until = NULL WHERE id = ?",
        (hash_pin(pin), member_id),
    )
    conn.commit()
    return pin


def update_account(conn, member_id: int, name: str, owner_name: str) -> None:
    """Rename a member's bracket / owner name. Raises ValueError on bad/duplicate input."""
    name = (name or "").strip()
    owner_name = (owner_name or "").strip()
    if not valid_bracket_name(name):
        raise ValueError("Bracket name must be 2–40 chars (letters, numbers, spaces, _ - ').")
    if not valid_owner_name(owner_name):
        raise ValueError("Enter your name (1–40 chars).")
    dup = conn.execute(
        "SELECT 1 FROM member WHERE bracket_name = ? AND id != ?", (name, member_id)
    ).fetchone()
    if dup:
        raise ValueError("That bracket name is taken — pick another.")
    conn.execute(
        "UPDATE member SET bracket_name = ?, owner_name = ? WHERE id = ?",
        (name, owner_name, member_id),
    )
    conn.commit()


def login(conn, name: str, pin: str) -> dict:
    """Verify name+PIN. Raises ValueError on failure (with rate limiting)."""
    row = conn.execute("SELECT * FROM member WHERE bracket_name = ?", ((name or "").strip(),)).fetchone()
    if not row:
        raise ValueError("No bracket with that name.")
    member = dict(row)
    if _is_locked_out(member):
        raise ValueError("Too many attempts — try again in a few minutes.")
    try:
        _ph.verify(member["pin_hash"], pin or "")
    except VerifyMismatchError:
        failed = member["failed_logins"] + 1
        lockout = None
        if failed >= config.MAX_FAILED_LOGINS:
            from datetime import timedelta
            lockout = iso(now_utc() + timedelta(minutes=config.LOCKOUT_MINUTES))
            failed = 0
        conn.execute(
            "UPDATE member SET failed_logins = ?, lockout_until = ? WHERE id = ?",
            (failed, lockout, member["id"]),
        )
        conn.commit()
        raise ValueError("Incorrect PIN.")
    conn.execute(
        "UPDATE member SET failed_logins = 0, lockout_until = NULL, last_login_at = ? WHERE id = ?",
        (iso(now_utc()), member["id"]),
    )
    conn.commit()
    return member


def admin_login(conn, pin: str) -> bool:
    pool = get_pool(conn)
    try:
        _ph.verify(pool["admin_pin_hash"], pin or "")
        return True
    except VerifyMismatchError:
        return False
