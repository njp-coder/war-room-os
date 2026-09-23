"""Sign-in and orgs: GitHub OAuth, server-side sessions, org creation and invites by GitHub handle.

Every /api route needs a session except the few in PUBLIC. The session is an opaque random token in an HttpOnly
cookie; only its SHA-256 is stored. Demo mode (WARROOM_DEMO_MODE=1) brings back the persona picker so a demo can
show the same screen as a junior and a senior; it is off unless set, and the web shows a banner while it is on.

Env: GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET (a GitHub OAuth App), WARROOM_PUBLIC_URL (the web origin, used for the
OAuth callback and the Secure flag; derived from the request when unset), WARROOM_DEMO_MODE.
"""
from __future__ import annotations

import contextvars
import hashlib
import os
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import db

router = APIRouter(prefix="/api")

COOKIE = "wr_session"
SESSION_DAYS = 14
STATE_TTL = 600
PUBLIC = {"/api/auth/config", "/api/auth/github/start", "/api/auth/github/callback", "/api/auth/logout", "/api/health"}
ORG_ROLES = {"admin", "member"}

_session_user: contextvars.ContextVar[str | None] = contextvars.ContextVar("session_user", default=None)

db.x("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, user TEXT, created REAL, expires REAL, last_seen REAL, agent TEXT)")
db.x("CREATE TABLE IF NOT EXISTS oauth_states(state TEXT PRIMARY KEY, next TEXT, created REAL)")
for col in ("github_id TEXT", "joined REAL", "avatar TEXT", "invited_by TEXT"):
    try:
        db.x(f"ALTER TABLE users ADD COLUMN {col}")
    except Exception:
        pass  # already there


def demo_mode() -> bool:
    return os.environ.get("WARROOM_DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def github_ready() -> bool:
    return bool(os.environ.get("GITHUB_CLIENT_ID") and os.environ.get("GITHUB_CLIENT_SECRET"))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _public_url(request: Request) -> str:
    env = os.environ.get("WARROOM_PUBLIC_URL", "").strip().rstrip("/")
    if env:
        return env
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:3000"
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host.split(',')[0].strip()}"


def _safe_next(n: str | None) -> str:
    """Only same-site paths, so the sign-in redirect can't be turned into an open redirect."""
    return n if n and n.startswith("/") and not n.startswith("//") and "\\" not in n else "/"


def session_user(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    s = db.one("SELECT * FROM sessions WHERE id=?", (_hash(token),))
    if not s or s["expires"] < time.time():
        return None
    if time.time() - (s["last_seen"] or 0) > 300:
        db.x("UPDATE sessions SET last_seen=? WHERE id=?", (time.time(), s["id"]))
    return s["user"]


def user_id(x_user: str | None) -> str | None:
    """Who is making this request. The x-user header only counts in demo mode."""
    if demo_mode() and x_user:
        return x_user
    return _session_user.get()


async def middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api") or path in PUBLIC or request.method == "OPTIONS":
        return await call_next(request)
    uid = session_user(request)
    if request.method not in {"GET", "HEAD"} and uid:
        # Cookies ride along on cross-site form posts; refuse writes whose Origin isn't this site.
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != urlsplit(_public_url(request)).netloc:
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
    if not uid and not (demo_mode() and request.headers.get("x-user")):
        return JSONResponse({"detail": "Sign in first"}, status_code=401)
    tok = _session_user.set(uid)
    try:
        return await call_next(request)
    finally:
        _session_user.reset(tok)


# ---------------- sign-in ----------------

@router.get("/auth/config")
def config():
    return {"github": github_ready(), "demo": demo_mode()}


@router.get("/auth/github/start")
def github_start(request: Request, next: str | None = None):
    if not github_ready():
        raise HTTPException(503, "GitHub sign-in isn't set up. Add GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET to .env.")
    state = secrets.token_urlsafe(24)
    db.x("DELETE FROM oauth_states WHERE created < ?", (time.time() - STATE_TTL,))
    db.x("INSERT INTO oauth_states VALUES(?,?,?)", (state, _safe_next(next), time.time()))
    # A GitHub App gets its permissions from the install, not from scopes; an OAuth App still needs them.
    from . import ghapp
    q = {"client_id": os.environ["GITHUB_CLIENT_ID"], "redirect_uri": f"{_public_url(request)}/api/auth/github/callback",
         "state": state, "allow_signup": "true"}
    if not ghapp.configured():
        q["scope"] = "read:user user:email"
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{urlencode(q)}", status_code=302)


def _fail(request: Request, why: str) -> RedirectResponse:
    return RedirectResponse(f"/login?{urlencode({'error': why})}", status_code=302)


@router.get("/auth/github/callback")
def github_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error or not code or not state:
        return _fail(request, "GitHub sign-in was cancelled.")
    st = db.one("SELECT * FROM oauth_states WHERE state=?", (state,))
    db.x("DELETE FROM oauth_states WHERE state=?", (state,))
    if not st or time.time() - st["created"] > STATE_TTL:
        return _fail(request, "That sign-in link expired. Try again.")
    try:
        tok = httpx.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"}, timeout=15, data={
            "client_id": os.environ["GITHUB_CLIENT_ID"], "client_secret": os.environ["GITHUB_CLIENT_SECRET"], "code": code,
            "redirect_uri": f"{_public_url(request)}/api/auth/github/callback"}).json()
        access = tok.get("access_token")
        if not access:
            return _fail(request, tok.get("error_description") or "GitHub didn't accept the sign-in.")
        h = {"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"}
        gh = httpx.get("https://api.github.com/user", headers=h, timeout=15).json()
        emails = httpx.get("https://api.github.com/user/emails", headers=h, timeout=15)
        email = next((e["email"] for e in (emails.json() if emails.status_code == 200 else []) if e.get("primary") and e.get("verified")), None)
    except httpx.HTTPError:
        return _fail(request, "Couldn't reach GitHub. Try again.")
    if not gh.get("id") or not gh.get("login"):
        return _fail(request, "GitHub didn't return an account.")
    uid = _bind(str(gh["id"]), gh["login"], gh.get("name") or gh["login"], email or gh.get("email"), gh.get("avatar_url"))
    # Kept encrypted, and used for one thing: asking GitHub which App installs this person can see, so nobody can
    # connect a repo their own account can't reach. Replaced on every sign-in, deleted on sign-out of the last session.
    from . import staging
    staging.save_url(uid, access, "github_user")
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.x("INSERT INTO sessions VALUES(?,?,?,?,?,?)", (_hash(token), uid, now, now + SESSION_DAYS * 86400, now,
                                                       (request.headers.get("user-agent") or "")[:200]))
    u = db.one("SELECT org FROM users WHERE id=?", (uid,))
    r = RedirectResponse(st["next"] if u and u["org"] else "/welcome", status_code=302)
    r.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                 secure=_public_url(request).startswith("https://"), path="/")
    return r


def _bind(github_id: str, login: str, name: str, email: str | None, avatar: str | None) -> str:
    """The same person every time by GitHub id. A first sign-in claims the invite for their handle, if there is one."""
    u = db.one("SELECT id FROM users WHERE github_id=?", (github_id,))
    if u:
        db.x("UPDATE users SET github=?, avatar=?, email=COALESCE(?, email) WHERE id=?", (login, avatar, email, u["id"]))
        return u["id"]
    invite = db.one("SELECT id, name FROM users WHERE lower(github)=lower(?) AND github_id IS NULL AND org IS NOT NULL "
                    "ORDER BY invited_by IS NULL, rowid DESC LIMIT 1", (login,))
    if invite:
        db.x("UPDATE users SET github_id=?, github=?, joined=?, avatar=?, email=COALESCE(?, email), name=CASE WHEN name=github THEN ? ELSE name END "
             "WHERE id=?", (github_id, login, time.time(), avatar, email, name, invite["id"]))
        return invite["id"]
    uid = db.uid("usr")
    db.x("INSERT INTO users(id, org, name, email, title, org_role, shift, hours_today, client_facing, github, github_id, joined, avatar) "
         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (uid, None, name, email, "", None, "on", 0, 0, login, github_id, time.time(), avatar))
    return uid


@router.post("/auth/logout")
def logout(request: Request):
    token = request.cookies.get(COOKIE)
    if token:
        uid = session_user(request)
        db.x("DELETE FROM sessions WHERE id=?", (_hash(token),))
        if uid and not db.one("SELECT 1 FROM sessions WHERE user=?", (uid,)):
            from . import staging
            staging.remove(uid, "github_user")  # signed out everywhere: don't keep their GitHub token
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE, path="/")
    return r


# ---------------- orgs ----------------

def _me() -> dict:
    u = db.one("SELECT * FROM users WHERE id=?", (_session_user.get() or "",))
    if not u:
        raise HTTPException(401, "Sign in first")
    return u


def _admin() -> dict:
    u = _me()
    if not u["org"] or u["org_role"] != "admin":
        raise HTTPException(403, "Only org admins can manage people")
    return u


class OrgIn(BaseModel):
    name: str
    kind: str = "company"


@router.post("/orgs")
def create_org(body: OrgIn):
    u = _me()
    if u["org"]:
        raise HTTPException(409, "You already belong to an org")
    name = body.name.strip()
    if not 2 <= len(name) <= 80:
        raise HTTPException(400, "Give the org a name")
    if body.kind not in {"company", "agency"}:
        raise HTTPException(400, "Kind is company or agency")
    oid = db.uid("org")
    db.x("INSERT INTO orgs VALUES(?,?,?)", (oid, name, body.kind))
    db.x("UPDATE users SET org=?, org_role='admin' WHERE id=?", (oid, u["id"]))
    return {"id": oid}


@router.get("/org/people")
def people():
    u = _me()
    if not u["org"]:
        raise HTTPException(409, "Create or join an org first")
    rows = db.q("SELECT id, name, github, avatar, org_role, joined, title FROM users WHERE org=? AND org_role IN ('admin','member') "
                "ORDER BY joined IS NULL, org_role='admin' DESC, name", (u["org"],))
    members = [r for r in rows if r["joined"]]
    invited = [r for r in rows if not r["joined"] and r["github"]]
    known = {(r["github"] or "").lower() for r in rows}
    # People who work in this org's repos but haven't been invited: busiest first.
    seen: dict[str, int] = {}
    for n in db.q("SELECT n.label FROM nodes n JOIN projects p ON p.id=n.project JOIN edges e ON e.src=n.id AND e.project=n.project "
                  "WHERE p.org=? AND n.type='person' AND e.type IN ('authored','reviewed','owns')", (u["org"],)):
        login = n["label"]
        if login.lower() not in known and not login.endswith("[bot]"):
            seen[login] = seen.get(login, 0) + 1
    suggested = [{"github": k, "activity": v} for k, v in sorted(seen.items(), key=lambda kv: -kv[1])[:12]]
    return {"members": members, "invited": invited, "suggested": suggested, "can_manage": u["org_role"] == "admin", "me": u["id"]}


class InviteIn(BaseModel):
    github: str
    role: str = "member"


@router.post("/org/invites")
def invite(body: InviteIn):
    u = _admin()
    login = body.github.strip().lstrip("@")
    if not login or len(login) > 39 or not all(c.isalnum() or c == "-" for c in login):
        raise HTTPException(400, "That isn't a GitHub handle")
    if body.role not in ORG_ROLES:
        raise HTTPException(400, "Role is admin or member")
    if db.one("SELECT 1 FROM users WHERE org=? AND lower(github)=lower(?)", (u["org"], login)):
        raise HTTPException(409, f"@{login} is already in this org")
    uid = db.uid("usr")
    db.x("INSERT INTO users(id, org, name, email, title, org_role, shift, hours_today, client_facing, github, invited_by) "
         "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (uid, u["org"], login, None, "", body.role, "on", 0, 0, login, u["id"]))
    return {"id": uid}


@router.delete("/org/invites/{uid}")
def cancel_invite(uid: str):
    u = _admin()
    row = db.one("SELECT * FROM users WHERE id=? AND org=?", (uid, u["org"]))
    if not row or row["joined"]:
        raise HTTPException(404, "No such invite")
    db.x("DELETE FROM members WHERE user=?", (uid,))
    db.x("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}


class RoleIn(BaseModel):
    role: str


def _last_admin(org: str, uid: str) -> bool:
    return db.one("SELECT count(*) n FROM users WHERE org=? AND org_role='admin' AND joined IS NOT NULL AND id<>?", (org, uid))["n"] == 0


@router.post("/org/people/{uid}/role")
def set_role(uid: str, body: RoleIn):
    u = _admin()
    if body.role not in ORG_ROLES:
        raise HTTPException(400, "Role is admin or member")
    row = db.one("SELECT * FROM users WHERE id=? AND org=?", (uid, u["org"]))
    if not row:
        raise HTTPException(404)
    if row["org_role"] == "admin" and body.role != "admin" and _last_admin(u["org"], uid):
        raise HTTPException(400, "The org needs at least one admin")
    db.x("UPDATE users SET org_role=? WHERE id=?", (body.role, uid))
    return {"ok": True}


@router.delete("/org/people/{uid}")
def remove(uid: str):
    """Offboarding: out of the org and every project in it, signed out everywhere. Their history stays attributed."""
    u = _admin()
    row = db.one("SELECT * FROM users WHERE id=? AND org=?", (uid, u["org"]))
    if not row:
        raise HTTPException(404)
    if row["org_role"] == "admin" and _last_admin(u["org"], uid):
        raise HTTPException(400, "The org needs at least one admin")
    db.x("DELETE FROM members WHERE user=? AND project IN (SELECT id FROM projects WHERE org=?)", (uid, u["org"]))
    db.x("DELETE FROM sessions WHERE user=?", (uid,))
    from . import staging
    staging.remove(uid, "github_user")
    db.x("UPDATE users SET org=NULL, org_role=NULL WHERE id=?", (uid,))
    return {"ok": True}
