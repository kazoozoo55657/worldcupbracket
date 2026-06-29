# World Cup 2026 Family Bracket Pool

A March Madness–style prediction pool for the 2026 FIFA World Cup, self-hosted on the
Yggdrasil K3s homelab. Family members create a uniquely-named bracket, predict the
group stage and fill out the full knockout bracket up front, and earn escalating
points the further their picks survive.

## Format & scoring

2026 is a **48-team** tournament: 12 groups of 4 → group stage → a **32-team knockout**
(Round of 32 → Round of 16 → QF → SF → Final). There is no Round of 64.

**Group stage** (knockout-dominant): predict the **2 advancing teams** of each group.
`2 pts` per correct qualifier → max `12 × 2 × 2 = 48 pts`.

**Knockout** (doubling, "advancement model" — pick *which teams survive each round*,
not specific matchups, because 2026 pairings aren't known until groups finish):

| Layer | What you pick | Picks | Pts each | Layer max |
|-------|---------------|------:|---------:|----------:|
| Round of 32 | teams that **win** their R32 match (reach R16) | 16 | 1 | 16 |
| Round of 16 | teams that reach the QF | 8 | 2 | 16 |
| Quarterfinal | teams that reach the SF | 4 | 4 | 16 |
| Semifinal | teams that reach the Final | 2 | 8 | 16 |
| Champion | the winner | 1 | 16 | 16 |

Perfect knockout = **80 pts**. Picks are *monotonic*: a champion pick must also appear
in every earlier layer (the UI enforces this — each round draws from the previous round's
survivors).

**Live autofill:** the knockout bracket fills itself in as you click. Picking a winner
cascades that team straight into the next round (and onward), with no "Save bracket"
round-trip between rounds — the whole tree is recomputed client-side from one source of
truth (the real R32 field + your per-match winner picks), mirroring
`bracket_structure.resolve` so the final Save produces the same result. See
`static/bracket.js`.

**Locking:** a pick is editable only until the deciding game kicks off.
- *Group stage* — a group locks once its **last match is played** (every match
  `FINISHED`), so no one can revise their original group picks once the group is decided.
- *Knockout* — locks **per game, by official match number**, not per round. Every bracket
  game carries its FIFA match number (73–104, from `bracket_structure`) and is shown with
  an `M##` badge so the bracket mirrors the published draw. `scoring.real_knockout_status`
  reconstructs the *actual* tournament tree and pins each official number to its real
  fixture (R32 from group results; each later round from the real winners that feed it).
  The instant a real game kicks off (status `LIVE`/`FINISHED`) that numbered game freezes
  for **everyone** — 🔒 *played* tag, radios disabled, any submitted change ignored
  server-side — **regardless of who a member predicted would be in it**, even if their
  picks were already eliminated.

Late joiners can fill anything not yet started — already-played games are locked, so they
can neither interact with nor earn points on them.

**Champion banner:** once the Final is played, the scoreboard shows a "Congratulations
*<bracket>* — you're the Champion!" banner crowning the league winner (top of the
leaderboard), also noting which national team lifted the World Cup.

**Available points remaining:** the max additional points a member can still earn from
picks that are still alive and undecided. `total + available` only ever decreases.

## Architecture

- Single FastAPI + Jinja2 + htmx container, SQLite (WAL) on a hostPath PVC.
- `worldcup/poll.py` runs as a Kubernetes CronJob: `seed` (one-time) and `poll`
  (sync results). Both share one idempotent upsert (`worldcup/footballdata.py`) against
  football-data.org's `/competitions/WC/matches`: it derives teams + groups from the
  match payload, inserts knockout fixtures as the draw publishes them, and updates
  results — keyed by the feed's match id so reruns never duplicate. Scoring is computed
  live on read, so a poll just needs to land correct results (no separate rescore).
- Admin screen at `/admin` to manually enter/correct results (override wins, locks the
  row; the poller skips `result_locked` rows until you release them).

### Results sources
- **football-data.org** (primary): set `FOOTBALLDATA_API_KEY`; `seed`/`poll --source=footballdata`.
- **fdfile:PATH**: replay a saved raw football-data JSON (used by the mapping tests).
- **file:PATH**: a normalized `[{match,key,winner,...}]` list — a dead-simple manual feed.
- **synthetic**: `seed --synthetic` builds a deterministic 48-team tournament for dev/tests.

## Local dev

```bash
python3 -m venv worldcup-venv --without-pip
curl -fsSL https://bootstrap.pypa.io/get-pip.py | worldcup-venv/bin/python
worldcup-venv/bin/python -m pip install -r requirements.txt
# seed a synthetic tournament for testing
SETTINGS_ENV=dev worldcup-venv/bin/python -m worldcup.poll seed --synthetic
# run the app
worldcup-venv/bin/uvicorn worldcup.app:app --reload --port 8002
# run scoring tests
worldcup-venv/bin/python -m pytest tests/ -q   # or: worldcup-venv/bin/python -m unittest
```

Environment variables: `WC_DB_PATH` (default `./data/worldcup.db`), `WC_SESSION_SECRET`,
`WC_JOIN_CODE`, `WC_ADMIN_PIN`, `WC_POOL_NAME`, `FOOTBALLDATA_API_KEY`.
