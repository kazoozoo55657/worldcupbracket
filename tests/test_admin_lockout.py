import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    from worldcup.config import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "wc.db"))
    monkeypatch.setattr(config, "ADMIN_PIN", "4321")
    from worldcup import db
    db.init_db()
    return db, config


def test_admin_login_locks_out_after_max_failures(fresh_db):
    db, config = fresh_db
    from worldcup import auth
    conn = db.connect()
    for _ in range(config.MAX_FAILED_LOGINS):
        assert auth.admin_login(conn, "0000") is False
    # Locked now: even the right PIN is refused until the lockout expires.
    with pytest.raises(ValueError):
        auth.admin_login(conn, "4321")
    conn.execute("UPDATE pool SET admin_lockout_until = '2000-01-01T00:00:00Z'")
    assert auth.admin_login(conn, "4321") is True
    row = conn.execute("SELECT admin_failed_logins, admin_lockout_until FROM pool").fetchone()
    assert row["admin_failed_logins"] == 0 and row["admin_lockout_until"] is None


def test_rotating_secret_updates_admin_pin(fresh_db, monkeypatch):
    db, config = fresh_db
    from worldcup import auth
    monkeypatch.setattr(config, "ADMIN_PIN", "98765432")
    db.init_db()
    conn = db.connect()
    assert auth.admin_login(conn, "4321") is False
    assert auth.admin_login(conn, "98765432") is True
    assert auth.login(conn, "admin", "98765432")["is_admin"] == 1


def test_migrates_old_pool_table(tmp_path, monkeypatch):
    from worldcup.config import config
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE pool (id INTEGER PRIMARY KEY CHECK (id = 1), name TEXT NOT NULL,"
                " join_code TEXT NOT NULL, admin_pin_hash TEXT NOT NULL, settings_json TEXT NOT NULL DEFAULT '{}')")
    old.commit(); old.close()
    monkeypatch.setattr(config, "DB_PATH", str(path))
    from worldcup import db
    db.init_db()
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(pool)")}
    assert {"admin_failed_logins", "admin_lockout_until"} <= cols
