"""The fixed 2026 World Cup knockout bracket wiring (from FIFA's published draw).

The results feed gives no slot labels, so the bracket tree is built here. A side is:
  ("W", "E")              winner of Group E
  ("R", "C")              runner-up of Group C
  ("3", ["A","B","C"...]) best third-placed team from those candidate groups

For Round of 16 onward, a match's two sides are the winners of two earlier matches.
Scoring is unaffected by this wiring — it only drives the visual bracket + the
constrained dropdowns. Winners get stored per round in advancement_pick.
"""
from __future__ import annotations

# Round of 32 (matches 73–88). Third-place slots are always the "away" side.
R32_MATCHES = [
    {"no": 73, "home": ("R", "A"), "away": ("R", "B")},
    {"no": 74, "home": ("W", "E"), "away": ("3", ["A", "B", "C", "D", "F"])},
    {"no": 75, "home": ("W", "F"), "away": ("R", "C")},
    {"no": 76, "home": ("W", "C"), "away": ("R", "F")},
    {"no": 77, "home": ("W", "I"), "away": ("3", ["C", "D", "F", "G", "H"])},
    {"no": 78, "home": ("R", "E"), "away": ("R", "I")},
    {"no": 79, "home": ("W", "A"), "away": ("3", ["C", "E", "F", "H", "I"])},
    {"no": 80, "home": ("W", "L"), "away": ("3", ["E", "H", "I", "J", "K"])},
    {"no": 81, "home": ("W", "D"), "away": ("3", ["B", "E", "F", "I", "J"])},
    {"no": 82, "home": ("W", "G"), "away": ("3", ["A", "E", "H", "I", "J"])},
    {"no": 83, "home": ("R", "K"), "away": ("R", "L")},
    {"no": 84, "home": ("W", "H"), "away": ("R", "J")},
    {"no": 85, "home": ("W", "B"), "away": ("3", ["E", "F", "G", "I", "J"])},
    {"no": 86, "home": ("W", "J"), "away": ("R", "H")},
    {"no": 87, "home": ("W", "K"), "away": ("3", ["D", "E", "I", "J", "L"])},
    {"no": 88, "home": ("R", "D"), "away": ("R", "G")},
]

# Later rounds: each match is fed by the winners of two earlier matches.
R16_MATCHES = [
    {"no": 89, "feeds": (74, 77)}, {"no": 90, "feeds": (73, 75)},
    {"no": 91, "feeds": (76, 78)}, {"no": 92, "feeds": (79, 80)},
    {"no": 93, "feeds": (83, 84)}, {"no": 94, "feeds": (81, 82)},
    {"no": 95, "feeds": (86, 88)}, {"no": 96, "feeds": (85, 87)},
]
QF_MATCHES = [
    {"no": 97, "feeds": (89, 90)}, {"no": 98, "feeds": (93, 94)},
    {"no": 99, "feeds": (91, 92)}, {"no": 100, "feeds": (95, 96)},
]
SF_MATCHES = [{"no": 101, "feeds": (97, 98)}, {"no": 102, "feeds": (99, 100)}]
FINAL_MATCH = {"no": 104, "feeds": (101, 102)}

# round code -> ordered match list (for rendering columns)
ROUND_MATCHES = {
    "R32": R32_MATCHES, "R16": R16_MATCHES, "QF": QF_MATCHES,
    "SF": SF_MATCHES, "F": [FINAL_MATCH],
}
FED_MATCHES = R16_MATCHES + QF_MATCHES + SF_MATCHES + [FINAL_MATCH]
THIRD_PLACE_SLOTS = {m["no"]: m["away"][1] for m in R32_MATCHES if m["away"][0] == "3"}


# Two-sided layout for the visual bracket. The left half flows left->right toward the
# centre Final; the right half flows right->left. Each entry is (round, [match numbers
# top-to-bottom]) ordered so a parent sits centred between its two children.
LEFT_COLUMNS = [
    ("R32", [74, 77, 73, 75, 83, 84, 81, 82]),
    ("R16", [89, 90, 93, 94]),
    ("QF", [97, 98]),
    ("SF", [101]),
]
RIGHT_COLUMNS = [  # nearest-centre column first; the R32 column renders on the far right
    ("SF", [102]),
    ("QF", [99, 100]),
    ("R16", [91, 92, 95, 96]),
    ("R32", [76, 78, 79, 80, 86, 88, 85, 87]),
]

NO_TO_R32 = {m["no"]: m for m in R32_MATCHES}
NO_TO_FED = {m["no"]: m for m in FED_MATCHES}


def round_of(no: int) -> str:
    if 73 <= no <= 88:
        return "R32"
    if 89 <= no <= 96:
        return "R16"
    if 97 <= no <= 100:
        return "QF"
    if 101 <= no <= 102:
        return "SF"
    if no == 104:
        return "F"
    raise ValueError(f"unknown match no {no}")


def _pick_winner(home, away, winner_set):
    if home and home in winner_set:
        return home
    if away and away in winner_set:
        return away
    return None


def resolve(group_winner: dict, group_runner: dict, slot_pick: dict,
            round_winners: dict, real_winners: dict | None = None) -> tuple[dict, dict]:
    """Compute predicted participants + winners for every knockout match.

    Inputs are a member's picks:
      group_winner: {group_code: team_id}   (predicted 1st of each group)
      group_runner: {group_code: team_id}   (predicted 2nd of each group)
      slot_pick:    {match_no: team_id}      (predicted team for a 3rd-place slot)
      round_winners:{round_code: set(team_id)} (predicted match winners per round)
      real_winners: {match_no: team_id}      (the team that REALLY won a decided game)

    Returns (participants, winners):
      participants: {match_no: (home_team_id|None, away_team_id|None)}
      winners:      {match_no: team_id|None}

    A fed match's slot is filled by the member's predicted winner of the feeding
    game; if they made no pick AND that game is already decided, it falls back to the
    real winner of the matchup (``real_winners``), so a member who missed an early
    game can still pick the later rounds.
    """
    real_winners = real_winners or {}
    participants: dict[int, tuple] = {}
    winners: dict[int, int | None] = {}

    def side_team(spec, match_no):
        kind, val = spec
        if kind == "W":
            return group_winner.get(val)
        if kind == "R":
            return group_runner.get(val)
        if kind == "3":
            return slot_pick.get(match_no)
        return None

    def feed_team(feed_no):
        return winners.get(feed_no) or real_winners.get(feed_no)

    for m in R32_MATCHES:
        h = side_team(m["home"], m["no"])
        a = side_team(m["away"], m["no"])
        participants[m["no"]] = (h, a)
        winners[m["no"]] = _pick_winner(h, a, round_winners.get("R32", set()))

    for m in FED_MATCHES:
        h = feed_team(m["feeds"][0])
        a = feed_team(m["feeds"][1])
        participants[m["no"]] = (h, a)
        winners[m["no"]] = _pick_winner(h, a, round_winners.get(round_of(m["no"]), set()))

    return participants, winners


def build_from_match_choices(group_winner: dict, group_runner: dict, slot_pick: dict,
                             match_choice: dict, real_winners: dict | None = None
                             ) -> tuple[dict, dict, dict]:
    """Turn raw per-match winner choices into clean, consistent round-winner sets.

    A submitted winner counts only if it is actually one of that match's two
    predicted participants (so changing an upstream pick auto-invalidates stale
    downstream picks). When a feeding game has no pick but is already decided, its
    slot falls back to the real winner (``real_winners``) — mirroring ``resolve`` —
    so a later-round pick made after missing an early game still validates.
    Returns (round_winners, participants, winners).
    """
    real_winners = real_winners or {}
    participants: dict[int, tuple] = {}
    winners: dict[int, int | None] = {}

    def side_team(spec, match_no):
        kind, val = spec
        if kind == "W":
            return group_winner.get(val)
        if kind == "R":
            return group_runner.get(val)
        return slot_pick.get(match_no)

    def feed_team(feed_no):
        return winners.get(feed_no) or real_winners.get(feed_no)

    def choose(no, h, a):
        w = match_choice.get(no)
        return w if (w and w in (h, a)) else None

    for m in R32_MATCHES:
        h = side_team(m["home"], m["no"])
        a = side_team(m["away"], m["no"])
        participants[m["no"]] = (h, a)
        winners[m["no"]] = choose(m["no"], h, a)

    for m in FED_MATCHES:
        h = feed_team(m["feeds"][0])
        a = feed_team(m["feeds"][1])
        participants[m["no"]] = (h, a)
        winners[m["no"]] = choose(m["no"], h, a)

    round_winners: dict[str, set] = {}
    for no, w in winners.items():
        if w:
            round_winners.setdefault(round_of(no), set()).add(w)
    return round_winners, participants, winners
