"""FastAPI application: join/login, group + bracket picks, leaderboard, admin."""
from __future__ import annotations

import hashlib
import secrets
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer, BadSignature

from . import auth, repo, rankings, bracket_structure, flags
from .config import config, KNOCKOUT_LAYERS, LAYER_BY_ROUND, KNOCKOUT_ROUNDS, parse_iso, now_utc
from .db import connect, init_db, get_pool
from .locks import compute_locks
from .scoring import TournamentState, score_member

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app = FastAPI(title="World Cup 2026 Bracket Pool")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Cache-bust static assets: a content hash in the URL means a new build serves a new
# URL, so Cloudflare/browser caches can't serve a stale stylesheet.
STATIC_VER = hashlib.md5((BASE_DIR / "static" / "style.css").read_bytes()).hexdigest()[:8]
templates.env.globals["static_ver"] = STATIC_VER

_csrf_signer = URLSafeSerializer(config.SESSION_SECRET, salt="wc-csrf")
CSRF_COOKIE = "wc_csrf"


@app.on_event("startup")
def _startup():
    init_db()


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _cookie_kwargs(max_age: int):
    return dict(httponly=True, samesite="lax", secure=not config.is_dev, max_age=max_age)


# ---- CSRF (double-submit with a signed httponly cookie) ----

@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    raw = request.cookies.get(CSRF_COOKIE)
    token = None
    if raw:
        try:
            token = _csrf_signer.loads(raw)
        except BadSignature:
            token = None
    fresh = token is None
    if fresh:
        token = secrets.token_urlsafe(16)
    request.state.csrf = token
    response = await call_next(request)
    if fresh:
        response.set_cookie(CSRF_COOKIE, _csrf_signer.dumps(token), **_cookie_kwargs(config.SESSION_MAX_AGE))
    return response


def check_csrf(request: Request, form_token: str):
    if not form_token or not secrets.compare_digest(form_token, request.state.csrf):
        raise HTTPException(status_code=403, detail="Invalid CSRF token. Reload and retry.")


# ---- session helpers ----

def current_session(request: Request) -> dict | None:
    return auth.read_session(request.cookies.get(auth.COOKIE_NAME, ""))


def require_member(request: Request) -> dict:
    sess = current_session(request)
    if not sess:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    with db() as conn:
        m = repo.get_member(conn, sess["m"])
    if not m:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return m


def require_admin(request: Request) -> dict:
    sess = current_session(request)
    if not sess or not sess.get("a"):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return sess


def ctx(request: Request, **kw):
    base = {"request": request, "csrf": request.state.csrf, "session": current_session(request)}
    base.update(kw)
    return base


# ---------------- public ----------------

@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with db() as conn:
        board = repo.leaderboard(conn)
        pool = dict(get_pool(conn))
    return templates.TemplateResponse(
        "home.html", ctx(request, board=board, pool=pool, layers=KNOCKOUT_LAYERS)
    )


@app.get("/join", response_class=HTMLResponse)
def join_form(request: Request, code: str = ""):
    return templates.TemplateResponse("join.html", ctx(request, prefill_code=code, error=None))


@app.post("/join")
def join_submit(
    request: Request,
    code: str = Form(""),
    bracket_name: str = Form(""),
    pin: str = Form(""),
    csrf_token: str = Form(""),
):
    check_csrf(request, csrf_token)
    with db() as conn:
        if not auth.check_join_code(conn, code):
            return templates.TemplateResponse(
                "join.html", ctx(request, prefill_code=code, error="Wrong join code."), status_code=400
            )
        try:
            member = auth.register(conn, bracket_name, pin)
        except ValueError as e:
            return templates.TemplateResponse(
                "join.html", ctx(request, prefill_code=code, error=str(e)), status_code=400
            )
    resp = RedirectResponse("/bracket", status_code=303)
    resp.set_cookie(
        auth.COOKIE_NAME, auth.make_session(member["id"], False), **_cookie_kwargs(config.SESSION_MAX_AGE)
    )
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", ctx(request, error=None))


@app.post("/login")
def login_submit(
    request: Request,
    bracket_name: str = Form(""),
    pin: str = Form(""),
    csrf_token: str = Form(""),
):
    check_csrf(request, csrf_token)
    with db() as conn:
        try:
            member = auth.login(conn, bracket_name, pin)
        except ValueError as e:
            return templates.TemplateResponse("login.html", ctx(request, error=str(e)), status_code=400)
    resp = RedirectResponse("/bracket", status_code=303)
    resp.set_cookie(
        auth.COOKIE_NAME, auth.make_session(member["id"], bool(member["is_admin"])),
        **_cookie_kwargs(config.SESSION_MAX_AGE),
    )
    return resp


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    check_csrf(request, csrf_token)
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


# ---------------- member picks ----------------

def _int(v):
    try:
        n = int(v)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _slot_label(spec):
    kind, val = spec
    if kind == "W":
        return f"Winner Group {val}"
    if kind == "R":
        return f"Runner-up Group {val}"
    return "3rd place: " + "/".join(val)


def _medal_map(conn):
    m = {}
    for g in repo.all_groups(conn):
        m.update(rankings.group_medals(repo.teams_in_group(conn, g["code"])))
    return m


def _build_bracket_view(conn, member):
    matches_rows = repo.all_matches(conn)
    locks = compute_locks(matches_rows)
    teams = repo.teams_by_id(conn)
    medals = _medal_map(conn)
    ranked = repo.member_group_ranked(conn, member["id"])
    state = TournamentState.from_matches(matches_rows)
    my = score_member(state, member["id"], member["bracket_name"],
                      repo.member_group_picks(conn, member["id"]),
                      repo.member_adv_picks(conn, member["id"]), member["joined_at"])

    def tview(t, medal=None):
        return {"id": t["id"], "name": t["name"], "medal": medal,
                "flag": flags.flag_img(t["name"]), "emoji": flags.flag_emoji(t["name"])}

    # Groups section: teams sorted by FIFA rank so the gold ★ reads top-down.
    groups_ctx = []
    for g in repo.all_groups(conn):
        gteams = sorted(repo.teams_in_group(conn, g["code"]),
                        key=lambda t: rankings.rank_of(t["name"]))
        r = ranked.get(g["code"], {})
        groups_ctx.append({
            "code": g["code"], "name": g["name"],
            "locked": locks["groups"].get(g["code"], False),
            "winner_id": r.get("winner"), "runner_id": r.get("runner"),
            "teams": [tview(t, medals.get(t["id"])) for t in gteams],
        })

    participants, winners = repo.resolve_member(conn, member["id"])

    def disp(tid):
        t = teams.get(tid) if tid else None
        return tview(t, medals.get(tid)) if t else None

    def side(no, which, tid):
        if tid:
            return {"team": disp(tid), "label": None}
        if no in bracket_structure.NO_TO_R32:
            spec = bracket_structure.NO_TO_R32[no]["home" if which == "home" else "away"]
            return {"team": None, "label": _slot_label(spec)}
        feed = bracket_structure.NO_TO_FED[no]["feeds"][0 if which == "home" else 1]
        return {"team": None, "label": f"Winner M{feed}"}

    def kmatch(no):
        h, a = participants.get(no, (None, None))
        rlocked = locks["rounds"].get(bracket_structure.round_of(no), False)
        return {"no": no, "home": side(no, "home", h), "away": side(no, "away", a),
                "winner_id": winners.get(no), "can_pick": bool(h and a) and not rlocked}

    def columns(spec):
        return [{"round": rc, "label": LAYER_BY_ROUND[rc]["label"],
                 "matches": [kmatch(n) for n in nos]} for rc, nos in spec]

    final_no = bracket_structure.FINAL_MATCH["no"]
    ko_open = any(any(participants.get(m["no"], (None, None))) for m in bracket_structure.R32_MATCHES)

    return dict(groups=groups_ctx, my=my, ranking_date=rankings.RANKING_DATE,
                ko_left=columns(bracket_structure.LEFT_COLUMNS),
                ko_right=columns(bracket_structure.RIGHT_COLUMNS),
                ko_final=kmatch(final_no), champion=disp(winners.get(final_no)),
                ko_open=ko_open)


@app.get("/groups")
def groups_redirect():
    return RedirectResponse("/bracket", status_code=307)


@app.get("/bracket", response_class=HTMLResponse)
def bracket_page(request: Request, member: dict = Depends(require_member)):
    with db() as conn:
        view = _build_bracket_view(conn, member)
    return templates.TemplateResponse("bracket.html", ctx(request, member=member, **view))


@app.post("/bracket")
async def save_bracket(request: Request, member: dict = Depends(require_member)):
    form = await request.form()
    check_csrf(request, form.get("csrf_token", ""))
    with db() as conn:
        locks = compute_locks(repo.all_matches(conn))
        _, existing_winners = repo.resolve_member(conn, member["id"])
        # Group winner/runner picks (skip locked groups).
        for g in repo.all_groups(conn):
            gc = g["code"]
            if locks["groups"].get(gc):
                continue
            try:
                repo.set_group_pick(conn, member["id"], gc,
                                    _int(form.get(f"gw_{gc}")), _int(form.get(f"gr_{gc}")), locks)
            except repo.PickError:
                pass  # ignore inconsistent group input; user can re-pick
        # Match winners are validated against the REAL R32 field (auto-filled from
        # football-data), so the knockout is independent of group-stage picks.
        gw, gr, slots = repo.actual_r32_fillers(conn)
        match_choice = {}
        for rc, mlist in bracket_structure.ROUND_MATCHES.items():
            locked = locks["rounds"].get(rc)
            for m in mlist:
                no = m["no"]
                match_choice[no] = existing_winners.get(no) if locked else _int(form.get(f"win_{no}"))
        round_winners, _, _ = bracket_structure.build_from_match_choices(gw, gr, slots, match_choice)
        repo.set_round_winners(conn, member["id"], round_winners)
    return RedirectResponse("/bracket", status_code=303)


# ---------------- admin ----------------

@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_form(request: Request):
    return templates.TemplateResponse("admin_login.html", ctx(request, error=None))


@app.post("/admin/login")
def admin_login_submit(request: Request, pin: str = Form(""), csrf_token: str = Form("")):
    check_csrf(request, csrf_token)
    with db() as conn:
        if not auth.admin_login(conn, pin):
            return templates.TemplateResponse(
                "admin_login.html", ctx(request, error="Incorrect admin PIN."), status_code=400
            )
        admin = repo.get_member_by_name(conn, "admin")
    resp = RedirectResponse("/admin", status_code=303)
    mid = admin["id"] if admin else 0
    resp.set_cookie(auth.COOKIE_NAME, auth.make_session(mid, True), **_cookie_kwargs(config.SESSION_MAX_AGE))
    return resp


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, _: dict = Depends(require_admin)):
    with db() as conn:
        teams = repo.teams_by_id(conn)
        members = repo.all_members(conn)
        matches = sorted(
            repo.all_matches(conn),
            key=lambda m: (parse_iso(m["kickoff_at"]), m["round"]),
        )
    return templates.TemplateResponse(
        "admin.html", ctx(request, matches=matches, teams=teams, members=members,
                          round_order=["GROUP"] + KNOCKOUT_ROUNDS)
    )


@app.post("/admin/member/{member_id}/delete")
def admin_delete_member(request: Request, member_id: int, csrf_token: str = Form(""),
                        _: dict = Depends(require_admin)):
    check_csrf(request, csrf_token)
    with db() as conn:
        # is_admin=0 guard so the admin account can't be deleted; picks cascade.
        conn.execute("DELETE FROM member WHERE id = ? AND is_admin = 0", (member_id,))
        conn.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/match/{match_id}")
def admin_set_result(
    request: Request,
    match_id: int,
    home_team_id: int = Form(0),
    away_team_id: int = Form(0),
    home_score: str = Form(""),
    away_score: str = Form(""),
    winner_team_id: int = Form(0),
    went_to_pens: str = Form(""),
    status: str = Form("FINISHED"),
    csrf_token: str = Form(""),
    _: dict = Depends(require_admin),
):
    check_csrf(request, csrf_token)
    hs = int(home_score) if home_score.strip().isdigit() else None
    as_ = int(away_score) if away_score.strip().isdigit() else None
    with db() as conn:
        conn.execute(
            "UPDATE match SET home_team_id=?, away_team_id=?, home_score=?, away_score=?, "
            "winner_team_id=?, went_to_pens=?, status=?, result_locked=1, result_source='admin', "
            "updated_at=? WHERE id=?",
            (
                home_team_id or None, away_team_id or None, hs, as_,
                winner_team_id or None, 1 if went_to_pens else 0, status,
                now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"), match_id,
            ),
        )
        conn.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/match/{match_id}/release")
def admin_release(request: Request, match_id: int, csrf_token: str = Form(""), _: dict = Depends(require_admin)):
    check_csrf(request, csrf_token)
    with db() as conn:
        conn.execute("UPDATE match SET result_locked=0 WHERE id=?", (match_id,))
        conn.commit()
    return RedirectResponse("/admin", status_code=303)
