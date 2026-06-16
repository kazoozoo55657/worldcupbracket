"""Runtime configuration, read from environment variables."""
import os
from datetime import datetime, timezone

# Knockout layers: round code -> points per correct pick, and how many to pick.
# Each layer = "which teams WIN this round / advance out of it".
KNOCKOUT_LAYERS = [
    {"round": "R32", "label": "Round of 32", "value": 1, "size": 16},
    {"round": "R16", "label": "Round of 16", "value": 2, "size": 8},
    {"round": "QF", "label": "Quarterfinal", "value": 4, "size": 4},
    {"round": "SF", "label": "Semifinal", "value": 8, "size": 2},
    {"round": "F", "label": "Champion", "value": 16, "size": 1},
]
KNOCKOUT_ROUNDS = [layer["round"] for layer in KNOCKOUT_LAYERS]
LAYER_BY_ROUND = {layer["round"]: layer for layer in KNOCKOUT_LAYERS}

# Group stage: a lighter, separate scoring track (the knockout is independent and
# weighted more heavily). 1 pt per correctly-predicted advancing team -> 24 max,
# vs the 80-pt knockout (~77% of points live in the knockout).
GROUP_POINTS_PER_QUALIFIER = 1
GROUP_QUALIFIERS_PER_GROUP = 2  # top 2 advance


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


class Config:
    DB_PATH = _env("WC_DB_PATH", "./data/worldcup.db")
    SESSION_SECRET = _env("WC_SESSION_SECRET", "dev-insecure-change-me")
    SESSION_MAX_AGE = int(_env("WC_SESSION_MAX_AGE", str(60 * 60 * 24 * 30)))  # 30 days
    # Used only when bootstrapping the pool row on first init.
    POOL_NAME = _env("WC_POOL_NAME", "Family World Cup 2026")
    JOIN_CODE = _env("WC_JOIN_CODE", "kickoff2026")
    ADMIN_PIN = _env("WC_ADMIN_PIN", "0000")
    FOOTBALLDATA_API_KEY = _env("FOOTBALLDATA_API_KEY")
    # PIN brute-force protection.
    MAX_FAILED_LOGINS = 5
    LOCKOUT_MINUTES = 15

    @property
    def is_dev(self) -> bool:
        return self.SESSION_SECRET == "dev-insecure-change-me"


config = Config()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    """Parse the ISO-8601 UTC strings we store (tolerant of 'Z' suffix)."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
