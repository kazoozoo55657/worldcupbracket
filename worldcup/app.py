"""FastAPI application: join/login, group + bracket picks, leaderboard, admin."""
from __future__ import annotations

import secrets
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer, BadSignature

from . import auth, repo
from .config import config, KNOCKOUT_LAYERS, LAYER_BY_ROUND, KNOCKOUT_ROUNDS, parse_iso, now_utc
from .db import connect, init_db, get_pool
from .locks import compute_locks
from .scoring import TournamentState, score_member

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app = FastAPI(title="World Cup 2026 Bracket Pool")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

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

@app.get("/groups", response_class=HTMLResponse)
def groups_page(request: Request, member: dict = Depends(require_member)):
    with db() as conn:
        groups = repo.all_groups(conn)
        picks = repo.member_group_picks(conn, member["id"])
        locks = compute_locks(repo.all_matches(conn))
        teams = {g["code"]: repo.teams_in_group(conn, g["code"]) for g in groups}
    return templates.TemplateResponse(
        "groups.html",
        ctx(request, member=member, groups=groups, teams=teams, picks=picks, locks=locks),
    )


@app.post("/groups/{grp}")
def save_group(
    request: Request,
    grp: str,
    team: list[int] = Form(default=[]),
    csrf_token: str = Form(""),
    member: dict = Depends(require_member),
):
    check_csrf(request, csrf_token)
    with db() as conn:
        locks = compute_locks(repo.all_matches(conn))
        try:
            repo.save_group_pick(conn, member["id"], grp, team, locks)
        except repo.PickError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse("/groups", status_code=303)


@app.get("/bracket", response_class=HTMLResponse)
def bracket_page(request: Request, member: dict = Depends(require_member)):
    with db() as conn:
        matches = repo.all_matches(conn)
        teams = repo.teams_by_id(conn)
        all_teams = repo.all_teams(conn)
        adv = repo.member_adv_picks(conn, member["id"])
        locks = compute_locks(matches)
        state = TournamentState.from_matches(matches)
        gp = repo.member_group_picks(conn, member["id"])
        my = score_member(state, member["id"], member["bracket_name"], gp, adv, member["joined_at"])
    # Candidate pool per layer: R32 from all teams; deeper from previous round's picks.
    pools = {}
    prev = None
    for layer in KNOCKOUT_LAYERS:
        r = layer["round"]
        if prev is None:
            pools[r] = all_teams
        else:
            pools[r] = [teams[t] for t in sorted(adv.get(prev, set())) if t in teams]
        prev = r
    return templates.TemplateResponse(
        "bracket.html",
        ctx(request, member=member, layers=KNOCKOUT_LAYERS, pools=pools, adv=adv,
            locks=locks, teams=teams, state=state, my=my),
    )


@app.post("/bracket/{rnd}")
def save_bracket(
    request: Request,
    rnd: str,
    team: list[int] = Form(default=[]),
    csrf_token: str = Form(""),
    member: dict = Depends(require_member),
):
    check_csrf(request, csrf_token)
    with db() as conn:
        locks = compute_locks(repo.all_matches(conn))
        try:
            repo.save_adv_pick(conn, member["id"], rnd, team, locks)
        except repo.PickError as e:
            raise HTTPException(status_code=400, detail=str(e))
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
        matches = sorted(
            repo.all_matches(conn),
            key=lambda m: (parse_iso(m["kickoff_at"]), m["round"]),
        )
    return templates.TemplateResponse(
        "admin.html", ctx(request, matches=matches, teams=teams, round_order=["GROUP"] + KNOCKOUT_ROUNDS)
    )


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
