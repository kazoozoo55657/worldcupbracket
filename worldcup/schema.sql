-- World Cup 2026 bracket pool schema (SQLite).
-- All timestamps are ISO-8601 UTC strings (e.g. '2026-06-11T20:00:00Z').

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Single-row table describing the family pool.
CREATE TABLE IF NOT EXISTS pool (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    name           TEXT NOT NULL,
    join_code      TEXT NOT NULL,            -- shared secret carried in the invite link
    admin_pin_hash TEXT NOT NULL,            -- owner login
    settings_json  TEXT NOT NULL DEFAULT '{}' -- point values etc. (data, not hardcoded)
);

CREATE TABLE IF NOT EXISTS member (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    bracket_name  TEXT NOT NULL UNIQUE,       -- unique display name + login id
    pin_hash      TEXT NOT NULL,              -- argon2id
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    joined_at     TEXT NOT NULL,              -- drives late-join tiebreak
    last_login_at TEXT,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    lockout_until TEXT
);

-- Static tournament structure (seeded once).
CREATE TABLE IF NOT EXISTS grp (
    code     TEXT PRIMARY KEY,               -- 'A'..'L'
    name     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    code    TEXT,                             -- FIFA 3-letter
    ext_id  TEXT UNIQUE,                      -- id from the results feed
    grp     TEXT REFERENCES grp(code)         -- group letter (NULL if unknown)
);

-- Every fixture. Knockout rows exist up front with NULL teams until pairings resolve.
-- round ∈ {GROUP, R32, R16, QF, SF, F}.
CREATE TABLE IF NOT EXISTS match (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ext_id         TEXT UNIQUE,              -- feed id; idempotent upsert key
    round          TEXT NOT NULL,
    grp_code       TEXT REFERENCES grp(code),-- group matches only
    slot           TEXT,                     -- stable label e.g. 'R32-1', 'F'
    home_team_id   INTEGER REFERENCES team(id),
    away_team_id   INTEGER REFERENCES team(id),
    kickoff_at     TEXT NOT NULL,            -- per-match lock time (UTC ISO)
    status         TEXT NOT NULL DEFAULT 'SCHEDULED', -- SCHEDULED|LIVE|FINISHED
    home_score     INTEGER,
    away_score     INTEGER,
    winner_team_id INTEGER REFERENCES team(id), -- penalty-shootout winner recorded here
    went_to_pens   INTEGER NOT NULL DEFAULT 0,
    result_locked  INTEGER NOT NULL DEFAULT 0,   -- 1 = admin override; poller skips
    result_source  TEXT,                     -- 'api:football-data' | 'api:openfootball' | 'admin'
    updated_at     TEXT
);

-- Group-stage prediction: the 2 teams a member thinks advance from a group.
-- rank: 1 = predicted winner (feeds the bracket's 1X slots), 2 = runner-up (2X slots).
CREATE TABLE IF NOT EXISTS group_pick (
    member_id INTEGER NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    grp_code  TEXT NOT NULL REFERENCES grp(code),
    team_id   INTEGER NOT NULL REFERENCES team(id),
    rank      INTEGER,                       -- 1=winner, 2=runner-up
    PRIMARY KEY (member_id, grp_code, team_id)
);

-- Predicted team for a third-place R32 slot (the 8 "best 3rd place" matches).
-- match_no is the FIFA match number (e.g. 74); see bracket_structure.THIRD_PLACE_SLOTS.
CREATE TABLE IF NOT EXISTS slot_pick (
    member_id INTEGER NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    match_no  INTEGER NOT NULL,
    team_id   INTEGER NOT NULL REFERENCES team(id),
    PRIMARY KEY (member_id, match_no)
);

-- Knockout advancement prediction. round ∈ {R32,R16,QF,SF,F}:
-- the teams the member predicts WIN that round (i.e. advance out of it).
CREATE TABLE IF NOT EXISTS advancement_pick (
    member_id INTEGER NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    round     TEXT NOT NULL,
    team_id   INTEGER NOT NULL REFERENCES team(id),
    PRIMARY KEY (member_id, round, team_id)
);

CREATE INDEX IF NOT EXISTS idx_match_round ON match(round);
CREATE INDEX IF NOT EXISTS idx_team_grp ON team(grp);
