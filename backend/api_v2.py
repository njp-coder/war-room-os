"""Tenancy, projects, context, releases, bugs, tests. Every project route checks membership.

Who is asking comes from the session (see auth.py); the `x-user` header only counts in demo mode.
"""
from __future__ import annotations

import json
import re
import threading
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import auth, context, db, github, graph, releases
from .memory import memory_for

router = APIRouter(prefix="/api")

WRITE = {"owner", "lead", "engineer"}
TEST = {"owner", "lead", "engineer", "tester"}
LEAD = {"owner", "lead"}
READ_ALL = {"owner", "lead", "engineer", "tester", "support", "viewer", "client_guest"}


def me(x_user: str | None) -> dict:
    u = db.one("SELECT * FROM users WHERE id=?", (auth.user_id(x_user) or "",))
    if not u:
        raise HTTPException(401, "Sign in first")
    return u


def need(user: dict, project: str, allowed: set[str]) -> str:
    role = db.role_of(user["id"], project)
    if role not in allowed:
        raise HTTPException(403, "You don't have access to this project" if role is None else f"Your role ({role}) can't do this")
    return role


def _no_secret(msg: str) -> str:
    """Driver errors can echo the connection URL; never send a password back to the browser."""
    msg = re.sub(r"//([^:/@\s]+):[^@\s]*@", r"//\1:****@", msg)
    return msg.split("\n")[0]


def _owner_row(owner_type: str, owner_id: str) -> dict:
    """A bug, test or incident by id. The table comes from a fixed map, never from the request."""
    table = {"bug": "bugs", "test": "tests", "incident": "incidents"}.get(owner_type)
    if not table:
        raise HTTPException(404)
    row = db.one(f"SELECT * FROM {table} WHERE id=?", (owner_id,))
    if not row:
        raise HTTPException(404)
    return row


def rel_or_404(rid: str) -> dict:
    r = db.one("SELECT * FROM releases WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404)
    return r


# ---------------- identity and home ----------------

@router.get("/users")
def users():
    """The demo persona picker. Outside demo mode there is nothing to pick."""
    if not auth.demo_mode():
        raise HTTPException(404)
    return db.q("SELECT id, name, title, org_role FROM users WHERE org IS NOT NULL ORDER BY org_role='admin' DESC, name")


@router.get("/me")
def whoami(x_user: str | None = Header(None)):
    u = me(x_user)
    org = db.one("SELECT name FROM orgs WHERE id=?", (u["org"],)) if u["org"] else None
    return {**u, "org_name": org["name"] if org else None, "demo": auth.demo_mode()}


@router.get("/home")
def home(x_user: str | None = Header(None)):
    u = me(x_user)
    if not u["org"]:
        return {"needs_org": True, "me": u}
    org = db.one("SELECT * FROM orgs WHERE id=?", (u["org"],))
    projects = db.q("SELECT p.*, c.name AS client_name FROM projects p LEFT JOIN clients c ON c.id=p.client WHERE p.org=?", (u["org"],))
    visible = [dict(p, role=db.role_of(u["id"], p["id"])) for p in projects if db.role_of(u["id"], p["id"])]
    for p in visible:
        p["releases"] = db.q("SELECT id, name, stage, window_start FROM releases WHERE project=? ORDER BY window_start", (p["id"],))
        p["repos"] = db.q("SELECT full_name, status FROM repos WHERE project=?", (p["id"],))
    upcoming = sorted([dict(r, project=p["name"], project_id=p["id"]) for p in visible for r in p["releases"]
                       if r["stage"] != "closed"], key=lambda r: r["window_start"] or "")
    return {"me": u, "org": org, "clients": db.q("SELECT * FROM clients WHERE org=?", (u["org"],)),
            "projects": visible, "upcoming": upcoming, "can_create": u["org_role"] == "admin"}


class ClientIn(BaseModel):
    name: str


@router.post("/clients")
def create_client(body: ClientIn, x_user: str | None = Header(None)):
    u = me(x_user)
    if u["org_role"] != "admin":
        raise HTTPException(403, "Only org admins can add clients")
    cid = db.uid("cl")
    db.x("INSERT INTO clients VALUES(?,?,?)", (cid, u["org"], body.name.strip()))
    return db.one("SELECT * FROM clients WHERE id=?", (cid,))


class ProjectIn(BaseModel):
    name: str
    client: str | None = None
    description: str = ""


@router.post("/projects")
def create_project(body: ProjectIn, x_user: str | None = Header(None)):
    u = me(x_user)
    if u["org_role"] != "admin":
        raise HTTPException(403, "Only org admins can create projects")
    pid = db.uid("prj")
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?)", (pid, u["org"], body.client, body.name.strip(), body.description, time.time(), 0, None, "{}"))
    db.x("INSERT INTO members(project, user, role) VALUES(?,?,?)", (pid, u["id"], "owner"))
    db.event(pid, None, u["id"], "project_created", {"name": body.name})
    return {"id": pid}


@router.get("/projects/{pid}")
def project(pid: str, x_user: str | None = Header(None)):
    u = me(x_user)
    role = need(u, pid, READ_ALL)
    p = db.one("SELECT p.*, c.name AS client_name FROM projects p LEFT JOIN clients c ON c.id=p.client WHERE p.id=?", (pid,))
    repos = db.q("SELECT * FROM repos WHERE project=?", (pid,))
    for r in repos:
        r["progress"] = json.loads(r["progress"] or "{}")
    members = db.q("SELECT u.id, u.name, u.title, u.github, u.shift, u.hours_today, m.role FROM members m JOIN users u ON u.id=m.user WHERE m.project=? ORDER BY m.role", (pid,))
    rels = db.q("SELECT * FROM releases WHERE project=? ORDER BY stage='closed', window_start", (pid,))
    for r in rels:
        r["open_bugs"] = db.one("SELECT count(*) n FROM bugs WHERE release=? AND status NOT IN ('verified','closed')", (r["id"],))["n"]
        r["blockers"] = db.one("SELECT count(*) n FROM bugs WHERE release=? AND blocker=1 AND status NOT IN ('verified','closed')", (r["id"],))["n"]
    cov = context.coverage(pid) if role != "client_guest" else None
    return {"project": {**p, "settings": json.loads(p["settings"] or "{}")}, "role": role, "repos": repos, "members": members,
            "releases": rels, "coverage": cov, "memory": memory_for(pid).status(),
            "org_users": db.q("SELECT id, name, title FROM users WHERE org=?", (p["org"],)) if role in LEAD else []}


class MemberIn(BaseModel):
    user: str
    role: str


@router.post("/projects/{pid}/members")
def add_member(pid: str, body: MemberIn, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, LEAD)
    if body.role not in READ_ALL:
        raise HTTPException(400, "Unknown role")
    db.x("INSERT INTO members(project, user, role) VALUES(?,?,?) ON CONFLICT(project, user) DO UPDATE SET role=excluded.role", (pid, body.user, body.role))
    db.event(pid, None, u["id"], "member", {"user": body.user, "role": body.role})
    return {"ok": True}


class SettingsIn(BaseModel):
    staging_url: str | None = None
    client_facing: bool | None = None
    explorer_writes: bool | None = None


@router.post("/projects/{pid}/settings")
def settings(pid: str, body: SettingsIn, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, LEAD)
    p = db.one("SELECT settings FROM projects WHERE id=?", (pid,))
    s = json.loads(p["settings"] or "{}")
    if body.client_facing is not None:
        s["client_facing"] = body.client_facing
    if body.explorer_writes is not None:
        s["explorer_writes"] = body.explorer_writes
        db.event(pid, None, u["id"], "explorer_writes", {"note": "on" if body.explorer_writes else "off"})
    if body.staging_url:
        from . import netguard
        try:
            body.staging_url = netguard.safe_http(body.staging_url, "staging URL")
        except netguard.Blocked as e:
            raise HTTPException(400, str(e))
    db.x("UPDATE projects SET settings=?, staging_url=COALESCE(?, staging_url) WHERE id=?", (json.dumps(s), body.staging_url, pid))
    return {"ok": True}


# ---------------- GitHub and context ----------------

@router.get("/github/status")
def gh_status(x_user: str | None = Header(None)):
    me(x_user)
    return github.status()


@router.get("/github/repos")
def gh_repos(x_user: str | None = Header(None)):
    """Repos this person can connect. With the App that means the installs they can see; otherwise the token's repos."""
    u = me(x_user)
    from . import ghapp, staging
    if not ghapp.configured():
        try:
            return {"mode": "token", "installations": [{"account": None, "repos": github.my_repos()}]}
        except Exception as e:
            raise HTTPException(502, f"GitHub: {str(e)[:200]}")
    user_token = staging._url(u["id"], "github_user")
    if not user_token:
        raise HTTPException(409, "Sign in with GitHub again so War Room can see your installs")
    try:
        installs = ghapp.installations(user_token)
        return {"mode": "app", "installations": installs, **ghapp.app_meta()}
    except Exception as e:
        raise HTTPException(502, f"GitHub: {str(e)[:200]}")


class RepoIn(BaseModel):
    full_name: str


@router.post("/projects/{pid}/repos")
def connect_repo(pid: str, body: RepoIn, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, LEAD)
    name = body.full_name.strip().removeprefix("https://github.com/").strip("/").removesuffix(".git")
    from . import ghapp, staging
    if ghapp.configured():
        ghapp.forget(name)  # a fresh install of the App should be seen immediately
        if not ghapp.installation_for_repo(name):
            raise HTTPException(400, f"War Room isn't installed on {name}. Install it on that repo in GitHub, then try again.")
        user_token = staging._url(u["id"], "github_user")
        if not user_token or not ghapp.can_access(user_token, name):
            raise HTTPException(403, f"Your GitHub account can't reach {name} through an install of War Room")
    try:
        meta = github.get(f"/repos/{name}")
    except Exception as e:
        raise HTTPException(400, f"Can't read {name} on GitHub: {str(e)[:120]}")
    db.x("INSERT OR REPLACE INTO repos VALUES(?,?,?,?,?,?)", (pid, name, meta["default_branch"], "syncing", "{}", None))
    threading.Thread(target=context.ingest_repo, args=(pid, name), daemon=True).start()
    db.event(pid, None, u["id"], "repo_connected", {"repo": name})
    return {"ok": True, "repo": name}


@router.post("/projects/{pid}/repos/sync")
def sync_repo(pid: str, body: RepoIn, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, LEAD)
    db.x("UPDATE repos SET status='syncing' WHERE project=? AND full_name=?", (pid, body.full_name))
    threading.Thread(target=context.ingest_repo, args=(pid, body.full_name), daemon=True).start()
    return {"ok": True}


@router.get("/projects/{pid}/search")
def search(pid: str, q: str, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, WRITE | {"tester", "support"})
    mem = memory_for(pid)
    hits = mem.search(["knowledge", "changes", "people"], q, top_k=12)
    return {"hits": [{"id": h.id, "text": h.text[:240], "type": h.metadata.get("type"), "score": round(h.score, 3)} for h in hits],
            "ms": mem.stats.latencies_ms[-1] if mem.stats.latencies_ms else 0, "backend": mem.backend}


@router.get("/projects/{pid}/blast")
def blast(pid: str, node: str, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, WRITE | {"tester"})
    n = graph.node(pid, node)
    return {"node": n, **graph.blast_radius(pid, [node])}


# ---------------- releases ----------------

class ReleaseIn(BaseModel):
    name: str
    window_start: str = ""
    window_end: str = ""
    scope_type: str = "milestone"  # milestone | label | branch | prs | open
    scope_value: str = ""
    migration: bool = False


@router.get("/projects/{pid}/releases/scopes")
def release_scopes(pid: str, x_user: str | None = Header(None)):
    """What a release can be scoped to, from the synced PRs, so nobody has to type a milestone title exactly."""
    u = me(x_user)
    need(u, pid, WRITE)
    out: dict[str, dict[str, dict]] = {"milestone": {}, "label": {}, "branch": {}}
    for r in db.q("SELECT props FROM nodes WHERE project=? AND type='pr'", (pid,)):
        p = json.loads(r["props"] or "{}")
        keys = {"milestone": [p.get("milestone")] if p.get("milestone") else [], "label": p.get("labels") or [],
                "branch": [b for b in (p.get("head"), p.get("base")) if b]}
        for kind, vals in keys.items():
            for v in vals:
                e = out[kind].setdefault(v, {"value": v, "open": 0, "total": 0})
                e["total"] += 1
                e["open"] += p.get("state") == "open"
    # Open work first: those are the ones a new release is for.
    return {k: sorted(v.values(), key=lambda e: (-e["open"], -e["total"], e["value"]))[:30] for k, v in out.items()}


@router.post("/projects/{pid}/releases")
def create_release(pid: str, body: ReleaseIn, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, LEAD)
    rid = db.uid("rel")
    db.x("INSERT INTO releases(id, project, name, stage, window_start, window_end, scope_type, scope_value, migration, created) VALUES(?,?,?,?,?,?,?,?,?,?)",
         (rid, pid, body.name, "plan", body.window_start, body.window_end, body.scope_type, body.scope_value, int(body.migration), time.time()))
    db.event(pid, rid, u["id"], "release_created", body.model_dump())
    return {"id": rid}


@router.get("/releases/{rid}")
def release(rid: str, x_user: str | None = Header(None)):
    u = me(x_user)
    r = rel_or_404(rid)
    role = need(u, r["project"], READ_ALL)
    guest = role == "client_guest"
    prs = releases.linked_prs(r)
    bugs = db.q("SELECT * FROM bugs WHERE release=? ORDER BY status IN ('verified','closed'), blocker DESC, priority, created DESC", (rid,))
    for b in bugs:
        b["links"] = json.loads(b["links"] or "[]")
        b["evidence"] = json.loads(b["evidence"]) if (b["evidence"] or "").startswith("{") else b["evidence"]
        sp = (b["suggested_priority"] or "|").split("|", 1)
        b["suggested"], b["suggested_why"] = sp[0], sp[1] if len(sp) > 1 else ""
    names = {x["id"]: x["name"] for x in db.q("SELECT id, name FROM users")}
    return {
        "release": r, "role": role, "stages": releases.STAGES,
        "project": db.one("SELECT id, name, demo, staging_url FROM projects WHERE id=?", (r["project"],)),
        "prs": [] if guest else prs, "files": 0 if guest else len(releases.release_files(r)),
        "risks": [] if guest else db.q("SELECT * FROM risks WHERE release=? ORDER BY level='high' DESC, status", (rid,)),
        "tests": [] if guest else db.q("SELECT * FROM tests WHERE release=? ORDER BY status", (rid,)),
        "bugs": [dict(b, reporter_name=names.get(b["reporter"], b["reporter"]), assignee_name=names.get(b["assignee"]))
                 for b in bugs] if not guest else [{"title": b["title"], "env": b["env"], "status": b["status"]} for b in bugs],
        "gates": releases.gates(r),
        "decisions": db.q("SELECT * FROM decisions WHERE release=? ORDER BY created", (rid,)),
        "events": [] if guest else [dict(e, data=json.loads(e["data"])) for e in db.q(
            "SELECT * FROM events WHERE release=? ORDER BY seq DESC LIMIT 40", (rid,))],
        "testers": db.q("SELECT u.id, u.name FROM members m JOIN users u ON u.id=m.user WHERE m.project=? AND m.role='tester'", (r["project"],)),
        "assignable": db.q("SELECT u.id, u.name, u.shift, u.hours_today FROM members m JOIN users u ON u.id=m.user WHERE m.project=? AND m.role IN ('engineer','lead','owner')", (r["project"],)),
    }


class AdvanceIn(BaseModel):
    override_reason: str = ""


@router.post("/releases/{rid}/advance")
def advance(rid: str, body: AdvanceIn, x_user: str | None = Header(None)):
    u = me(x_user)
    r = rel_or_404(rid)
    need(u, r["project"], LEAD)
    try:
        return releases.advance(r, u["id"], body.override_reason)
    except ValueError as e:
        raise HTTPException(409, str(e))


class TextIn(BaseModel):
    text: str


@router.post("/releases/{rid}/rollback")
def rollback(rid: str, body: TextIn, x_user: str | None = Header(None)):
    u = me(x_user)
    r = rel_or_404(rid)
    need(u, r["project"], LEAD)
    db.x("UPDATE releases SET rollback_plan=? WHERE id=?", (body.text, rid))
    return {"ok": True}


@router.post("/releases/{rid}/premortem")
def premortem(rid: str, x_user: str | None = Header(None)):
    u = me(x_user)
    r = rel_or_404(rid)
    need(u, r["project"], WRITE)
    return releases.premortem(r)


@router.post("/releases/{rid}/tester/plan")
def tester_plan(rid: str, x_user: str | None = Header(None)):
    u = me(x_user)
    r = rel_or_404(rid)
    need(u, r["project"], TEST)
    from . import testing
    return testing.plan(r)


@router.post("/releases/{rid}/tester/run")
def tester_run(rid: str, x_user: str | None = Header(None)):
    u = me(x_user)
    r = rel_or_404(rid)
    need(u, r["project"], TEST)
    from .harness.loop import run
    from .harness.tools import Ctx
    ctx = Ctx(r["project"], release=r)
    res = run("tester", ctx, "release", rid, "test the release on staging")
    out = ctx.scratch.get("run") or {"ran": 0, "passed": 0, "failed": 0, "manual": 0}
    return {**out, "trace": res["steps"]}


class BugIn(BaseModel):
    title: str
    body: str = ""
    severity: str = "major"
    env: str = "staging"
    priority: str | None = None
    blocker: bool = False


@router.post("/releases/{rid}/bugs")
def report_bug(rid: str, body: BugIn, x_user: str | None = Header(None)):
    u = me(x_user)
    r = rel_or_404(rid)
    need(u, r["project"], TEST | {"support"})
    return releases.create_bug(r["project"], rid, body.env, body.title, body.body, body.severity, u["id"], "human",
                               body.priority, body.blocker)


class BugPatch(BaseModel):
    priority: str | None = None
    blocker: bool | None = None
    status: str | None = None
    assignee: str | None = None


@router.patch("/bugs/{bid}")
def patch_bug(bid: str, body: BugPatch, x_user: str | None = Header(None)):
    u = me(x_user)
    b = db.one("SELECT * FROM bugs WHERE id=?", (bid,))
    if not b:
        raise HTTPException(404)
    role = need(u, b["project"], TEST)
    if body.status in ("verified", "closed") and role not in ("tester", "lead", "owner"):
        raise HTTPException(403, "A tester or lead verifies fixes")
    for col in ("priority", "status", "assignee"):
        if getattr(body, col) is not None:
            db.x(f"UPDATE bugs SET {col}=? WHERE id=?", (getattr(body, col), bid))
    if body.blocker is not None:
        db.x("UPDATE bugs SET blocker=? WHERE id=?", (int(body.blocker), bid))
    if body.status in ("verified", "closed"):
        db.x("UPDATE bugs SET verified_by=? WHERE id=?", (u["id"], bid))
    db.event(b["project"], b["release"], u["id"], "bug_updated", {"bug": bid, **body.model_dump(exclude_none=True)})
    return db.one("SELECT * FROM bugs WHERE id=?", (bid,))


@router.post("/bugs/{bid}/investigate")
def investigate(bid: str, x_user: str | None = Header(None)):
    u = me(x_user)
    b = db.one("SELECT * FROM bugs WHERE id=?", (bid,))
    if not b:
        raise HTTPException(404)
    need(u, b["project"], WRITE | {"tester"})
    from .harness.loop import run
    from .harness.tools import Ctx
    rel = db.one("SELECT * FROM releases WHERE id=?", (b["release"],)) if b["release"] else None
    res = run("root-cause", Ctx(b["project"], bug=b, release=rel), "bug", bid, "re-investigate")
    return {"trace": res["steps"], "final": res["final"]}


class TestPatch(BaseModel):
    status: str
    result: str = ""


@router.post("/tests/{tid}")
def mark_test(tid: str, body: TestPatch, x_user: str | None = Header(None)):
    u = me(x_user)
    t = db.one("SELECT * FROM tests WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404)
    need(u, t["project"], TEST)
    db.x("UPDATE tests SET status=?, result=?, by=?, run_at=? WHERE id=?", (body.status, body.result or f"Marked by {u['name']}", u["id"], time.time(), tid))
    if body.status in ("passed", "failed") and t["risk"]:
        db.x("UPDATE risks SET status=?, tested_by=?, tested_at=? WHERE id=?",
             ("verified" if body.status == "passed" else "failed", u["id"], time.time(), t["risk"]))
    db.event(t["project"], t["release"], u["id"], "test_marked", {"test": tid, "status": body.status})
    return {"ok": True}


@router.post("/releases/{rid}/decisions")
def decision(rid: str, body: TextIn, x_user: str | None = Header(None)):
    u = me(x_user)
    r = rel_or_404(rid)
    need(u, r["project"], WRITE | {"tester"})
    did = db.uid("dec")
    db.x("INSERT INTO decisions VALUES(?,?,?,?,?,?,?)", (did, r["project"], rid, body.text, u["name"], time.time(), None))
    memory_for(r["project"]).add("human", [{"id": did, "text": body.text, "metadata": {"type": "decision", "release": rid}}])
    db.event(r["project"], rid, u["id"], "decision", {"text": body.text})
    return {"ok": True}


@router.post("/releases/{rid}/client_update/approve")
def approve_client_update(rid: str, x_user: str | None = Header(None)):
    u = me(x_user)
    r = rel_or_404(rid)
    need(u, r["project"], LEAD | {"support"})
    if not u["client_facing"]:
        raise HTTPException(403, "Only client-facing people approve client updates")
    db.event(r["project"], rid, u["id"], "client_update_approved", {})
    return {"ok": True}


# ---------------- context engine (agent-native) ----------------

class EngineIn(BaseModel):
    op: str  # understand | precedent | conventions | impact | explain | context
    task: str = ""
    target: str = ""
    state: dict = {}
    budget: int = 2000


@router.post("/projects/{pid}/engine")
def engine_op(pid: str, body: EngineIn, x_user: str | None = Header(None)):
    from . import engine

    u = me(x_user)
    need(u, pid, WRITE | {"tester"})
    t0 = time.perf_counter()
    if body.op == "context":
        out = engine.context(pid, body.task, body.state, body.budget)
    else:
        sections = {"understand": lambda: engine.understand(pid, body.task),
                    "precedent": lambda: engine.find_precedent(pid, body.task),
                    "conventions": lambda: engine.conventions(pid),
                    "impact": lambda: engine.impact(pid, body.target or body.task),
                    "explain": lambda: engine.explain(pid, body.target)}[body.op]()
        out = engine.pack(sections, body.budget)
    out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


@router.get("/projects/{pid}/engine/eval")
def engine_eval(pid: str, n: int = 25, k: int = 10, x_user: str | None = Header(None)):
    from . import engine

    u = me(x_user)
    need(u, pid, WRITE)
    return engine.evaluate(pid, n, k)


# ---------------- bug relay (agents + people) ----------------

def _bug(bid: str) -> dict:
    b = db.one("SELECT * FROM bugs WHERE id=?", (bid,))
    if not b:
        raise HTTPException(404)
    return b


def _card(b: dict) -> dict:
    from .pipeline import name
    who = b["assignee"] if b["stage"] in ("fixing", "fixed", "verified") else b["proposed"]
    return {"id": b["id"], "title": b["title"], "stage": b["stage"], "env": b["env"], "priority": b["priority"],
            "blocker": bool(b["blocker"]), "reporter_kind": b["reporter_kind"], "reporter": name(b["reporter"]) or b["reporter"],
            "who": name(who), "who_id": who, "agreed": b["stage"] in ("fixing", "fixed", "verified"),
            "eta": b["eta_owner"] or b["eta_agent"], "eta_agent": b["eta_agent"], "eta_owner": b["eta_owner"],
            "relay": json.loads(b["relay"] or "[]")}


@router.get("/releases/{rid}/board")
def release_board(rid: str, env: str = "staging", x_user: str | None = Header(None)):
    from . import pipeline
    u = me(x_user)
    r = rel_or_404(rid)
    role = need(u, r["project"], READ_ALL)
    bugs = db.q("SELECT * FROM bugs WHERE release=? AND env=? ORDER BY blocker DESC, priority, created", (rid, env))
    return {"role": role, "me": u["id"], "cards": [_card(b) for b in bugs], **pipeline.board(r)}


@router.get("/bugs/{bid}/room")
def bug_room(bid: str, x_user: str | None = Header(None)):
    from . import pipeline
    u = me(x_user)
    b = _bug(bid)
    role = need(u, b["project"], TEST | {"support"})
    ev = json.loads(b["evidence"]) if (b["evidence"] or "").startswith("{") else {"steps": [], "theory": None}
    rp = json.loads(b["repro"] or "{}") or {"steps": [], "endpoints": [], "fields": []}
    prop = json.loads(b["proposed_why"]) if (b["proposed_why"] or "").startswith("{") else None
    busy = pipeline.load(b["project"])
    people = db.q("""SELECT u.id, u.name, u.shift, u.hours_today FROM members m JOIN users u ON u.id=m.user
                     WHERE m.project=? AND m.role IN ('engineer','lead','owner')""", (b["project"],))
    return {"bug": _card(b), "body": b["body"], "severity": b["severity"], "role": role, "me": u["id"],
            "suggested": (b["suggested_priority"] or "|").split("|"), "evidence": ev, "repro": rp,
            "diagram": pipeline.diagram(b["project"], b, ev, rp),
            "proposal": prop, "eta_agent_why": b["eta_agent_why"],
            "people": [{"id": p["id"], "name": p["name"], "left": round(pipeline.HOURS_CAP - p["hours_today"] - busy.get(p["id"], 0), 1),
                        "shift": p["shift"]} for p in people],
            "release": b["release"], "project": b["project"],
            "files": __import__("backend.testing", fromlist=["x"]).attachments("bug", bid),
            "thread": __import__("backend.testing", fromlist=["x"]).notes("bug", bid),
            "help_test": db.one("SELECT id, claimed_by, status FROM tests WHERE bug=? AND target LIKE 'bug:%'", (bid,)),
            "runs": __import__("backend.harness.loop", fromlist=["x"]).runs_for("bug", bid),
            "advice": _advice_for("bug", bid)}


def _advice_for(owner_type: str, owner_id: str) -> list[dict]:
    from .pipeline import name
    out = []
    for a in db.q("SELECT a.*, e.name expert_name, e.teachers FROM advice a JOIN experts e ON e.id=a.expert WHERE a.owner_type=? AND a.owner_id=? ORDER BY a.at DESC",
                  (owner_type, owner_id)):
        out.append({"id": a["id"], "expert": a["expert_name"], "text": a["text"], "signoff": a["signoff"], "signoff_name": name(a["signoff"]) if a["signoff"] else None,
                    "verdict": a["verdict"], "learned_from": [t for t, _ in __import__("backend.experts", fromlist=["x"]).teachers(a["expert"], 3)], "lessons": len(json.loads(a["cites"] or "[]"))})
    return out


class AcceptIn(BaseModel):
    eta: float | None = None


@router.post("/bugs/{bid}/accept")
def bug_accept(bid: str, body: AcceptIn, x_user: str | None = Header(None)):
    from . import pipeline
    u = me(x_user)
    b = _bug(bid)
    role = need(u, b["project"], WRITE)
    if b["proposed"] != u["id"] and role not in LEAD:
        raise HTTPException(403, "Only the proposed owner or a lead can accept")
    pipeline.accept(bid, b["proposed"] if role in LEAD and b["proposed"] and b["proposed"] != u["id"] else u["id"], body.eta)
    return {"ok": True}


class ReasonIn(BaseModel):
    reason: str = ""


@router.post("/bugs/{bid}/decline")
def bug_decline(bid: str, body: ReasonIn, x_user: str | None = Header(None)):
    from . import pipeline
    u = me(x_user)
    b = _bug(bid)
    need(u, b["project"], WRITE)
    pipeline.decline(bid, u["id"], body.reason)
    return {"ok": True}


class ToIn(BaseModel):
    to: str


@router.post("/bugs/{bid}/reassign")
def bug_reassign(bid: str, body: ToIn, x_user: str | None = Header(None)):
    from . import pipeline
    u = me(x_user)
    b = _bug(bid)
    need(u, b["project"], LEAD)
    pipeline.reassign(bid, u["id"], body.to)
    return {"ok": True}


@router.post("/bugs/{bid}/fixed")
def bug_fixed(bid: str, x_user: str | None = Header(None)):
    from . import pipeline
    u = me(x_user)
    b = _bug(bid)
    role = need(u, b["project"], WRITE)
    if b["assignee"] != u["id"] and role not in LEAD:
        raise HTTPException(403, "Only the owner or a lead marks it fixed")
    pipeline.mark_fixed(bid, u["id"])
    return {"ok": True}


@router.post("/bugs/{bid}/verify")
def bug_verify(bid: str, x_user: str | None = Header(None)):
    from . import pipeline
    u = me(x_user)
    b = _bug(bid)
    role = need(u, b["project"], TEST)
    if role not in ("tester", "lead", "owner"):
        raise HTTPException(403, "A tester or lead verifies")
    pipeline.verify(bid, u["id"])
    return {"ok": True}


@router.post("/bugs/{bid}/repro/run")
def bug_repro_run(bid: str, x_user: str | None = Header(None)):
    from . import pipeline
    u = me(x_user)
    b = _bug(bid)
    need(u, b["project"], TEST)
    try:
        return pipeline.run_repro(b["project"], bid, u["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------- shared test session, evidence, threads ----------------

from fastapi import File, Form, UploadFile  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402


@router.get("/releases/{rid}/session")
def test_session(rid: str, x_user: str | None = Header(None)):
    from . import testing
    u = me(x_user)
    r = rel_or_404(rid)
    role = need(u, r["project"], TEST | {"support"})
    return {"role": role, "me": u["id"], **testing.session(r)}


@router.get("/tests/{tid}")
def test_room(tid: str, x_user: str | None = Header(None)):
    from . import testing
    from .pipeline import name
    u = me(x_user)
    t = db.one("SELECT * FROM tests WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404)
    role = need(u, t["project"], TEST | {"support"})
    return {"test": {**t, "how": json.loads(t["how"] or "[]"), "claimed_name": name(t["claimed_by"])}, "role": role, "me": u["id"],
            "evidence": testing.attachments("test", tid), "thread": testing.notes("test", tid),
            "testers": db.q("SELECT u.id, u.name FROM members m JOIN users u ON u.id=m.user WHERE m.project=? AND m.role='tester'", (t["project"],))}


@router.post("/tests/{tid}/claim")
def test_claim(tid: str, x_user: str | None = Header(None)):
    from . import testing
    u = me(x_user)
    t = db.one("SELECT * FROM tests WHERE id=?", (tid,))
    need(u, t["project"], TEST)
    testing.claim(tid, u["id"])
    return {"ok": True}


class FinishIn(BaseModel):
    status: str  # passed | failed
    notes: str = ""
    steps: list[str] | None = None
    save_recipe: bool = False


@router.post("/tests/{tid}/finish")
def test_finish(tid: str, body: FinishIn, x_user: str | None = Header(None)):
    from . import testing
    u = me(x_user)
    t = db.one("SELECT * FROM tests WHERE id=?", (tid,))
    need(u, t["project"], TEST)
    bug = testing.finish(tid, u["id"], body.status, body.notes, body.steps, body.save_recipe)
    return {"ok": True, "bug": bug["id"] if bug else None}


@router.post("/evidence/{owner_type}/{owner_id}")
async def upload_evidence(owner_type: str, owner_id: str, file: UploadFile = File(...), x_user: str | None = Header(None)):
    from . import testing
    u = me(x_user)
    table = {"bug": "bugs", "test": "tests", "incident": "incidents"}.get(owner_type)
    if not table:
        raise HTTPException(400)
    row = db.one(f"SELECT project FROM {table} WHERE id=?", (owner_id,))
    need(u, row["project"], TEST | {"support"})
    data = await file.read()
    if len(data) > 60_000_000:
        raise HTTPException(413, "Keep recordings under 60MB")
    att = testing.attach_file(row["project"], owner_type, owner_id, file.filename or "upload", data, file.content_type or "", u["name"])
    testing.note(row["project"], owner_type, owner_id, u["name"], "human", f"Attached {att['kind']}: {att['name']}")
    return att


@router.get("/files/{aid}")
def get_file(aid: str, x_user: str | None = Header(None)):
    """The caller is the session, never a user id in the query string."""
    user = me(x_user)
    a = db.one("SELECT * FROM attachments WHERE id=?", (aid,))
    if not a or not a["path"]:
        raise HTTPException(404)
    need(user, a["project"], READ_ALL)
    return FileResponse(a["path"], filename=a["name"])


class NoteIn(BaseModel):
    text: str


@router.get("/thread/{owner_type}/{owner_id}")
def thread(owner_type: str, owner_id: str, x_user: str | None = Header(None)):
    from . import testing
    u = me(x_user)
    row = _owner_row(owner_type, owner_id)
    need(u, row["project"], READ_ALL)
    return {"thread": testing.notes(owner_type, owner_id), "evidence": testing.attachments(owner_type, owner_id)}


@router.post("/thread/{owner_type}/{owner_id}")
def post_note(owner_type: str, owner_id: str, body: NoteIn, x_user: str | None = Header(None)):
    from . import testing
    u = me(x_user)
    row = _owner_row(owner_type, owner_id)
    need(u, row["project"], TEST | {"support"})
    testing.note(row["project"], owner_type, owner_id, u["name"], "human", body.text)
    _learn_from_reply(row["project"], owner_type, owner_id, u, body.text)
    return {"ok": True}


def _learn_from_reply(project: str, owner_type: str, owner_id: str, u: dict, text: str):
    """A senior answering the expert in a thread is teaching it. Juniors' replies stay in the thread only."""
    from . import experts
    a = db.one("SELECT * FROM advice WHERE owner_type=? AND owner_id=? ORDER BY at DESC", (owner_type, owner_id))
    if not a or len(text.strip()) < 25:
        return
    e = db.one("SELECT paths FROM experts WHERE id=?", (a["expert"],))
    area = json.loads(e["paths"]) if e else []
    if experts.is_senior(project, u["id"], area) or db.role_of(u["id"], project) in ("owner", "lead"):
        experts.teach(project, a["expert"], text, u.get("github") or u["name"], "thread", f"{owner_type}:{owner_id}")


@router.get("/releases/{rid}/runs")
def release_runs(rid: str, x_user: str | None = Header(None)):
    from .harness.loop import runs_for
    u = me(x_user)
    r = rel_or_404(rid)
    need(u, r["project"], TEST | {"support"})
    return runs_for("release", rid)


@router.get("/agents")
def agents_catalog(project: str = "", x_user: str | None = Header(None)):
    from .harness.loop import AGENTS, _tools
    from .harness.tools import REGISTRY
    me(x_user)
    out = []
    for k in ("tester", "root-cause", "repro", "dispatcher"):
        a = AGENTS[k]
        runs = db.q("SELECT steps, policy, started, ended FROM runs WHERE agent=?" + (" AND project=?" if project else ""),
                    (k, project) if project else (k,))
        calls = [s for r in runs for s in json.loads(r["steps"])]
        out.append({"id": k, "name": a["name"], "trust": a.get("trust"), "role": a.get("role"), "budget": a.get("budget_turns"),
                    "prompt": a["body"],
                    "tools": [{"name": t, "risk": REGISTRY[t].risk, "description": REGISTRY[t].description, "scope": REGISTRY[t].scope,
                               "calls": sum(1 for c in calls if c["tool"] == t)} for t in _tools(a) if t in REGISTRY],
                    "stats": {"runs": len(runs), "calls": len(calls), "blocked": sum(1 for c in calls if c.get("blocked")),
                              "errors": sum(1 for c in calls if not c.get("ok") and not c.get("blocked")),
                              "model_runs": sum(1 for r in runs if r["policy"] == "model")}})
    return out


# ---------------- staging database (read-only) ----------------

class DbIn(BaseModel):
    url: str


@router.get("/projects/{pid}/staging_db")
def staging_status(pid: str, x_user: str | None = Header(None)):
    from . import staging
    u = me(x_user)
    need(u, pid, WRITE | {"tester"})
    if not staging.connected(pid):
        return {"connected": False}
    try:
        return {"connected": True, "url": staging.masked(pid), "dialect": staging.dialect(pid), **staging.coverage(pid)}
    except Exception as e:
        return {"connected": True, "url": staging.masked(pid), "error": str(e)[:200]}


@router.post("/projects/{pid}/staging_db")
def staging_connect(pid: str, body: DbIn, x_user: str | None = Header(None)):
    from . import staging
    u = me(x_user)
    need(u, pid, LEAD)
    try:
        staging.verify_and_save(pid, body.url.strip())
        cov = staging.coverage(pid, refresh=False)
    except Exception as e:
        raise HTTPException(400, f"Couldn't connect: {_no_secret(str(e))[:240]}")
    db.event(pid, None, u["id"], "staging_db_connected", {"tables": cov["live_tables"]})
    return {"connected": True, "url": staging.masked(pid), **cov}


@router.delete("/projects/{pid}/staging_db")
def staging_disconnect(pid: str, x_user: str | None = Header(None)):
    from . import staging
    u = me(x_user)
    need(u, pid, LEAD)
    staging.remove(pid)
    return {"connected": False}


# ---------------- team from GitHub ----------------

@router.get("/projects/{pid}/team/candidates")
def team_candidates(pid: str, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, LEAD)
    mapped = {r["github"] for r in db.q("SELECT github FROM users WHERE github IS NOT NULL AND org=?", (u["org"],))}
    out = []
    for p in db.q("SELECT id, label FROM nodes WHERE project=? AND type='person'", (pid,)):
        login = p["label"]
        if login in mapped or login.endswith("[bot]"):
            continue
        wrote = len(graph.neighbors(pid, p["id"], {"authored"}, "out"))
        reviewed = len(graph.neighbors(pid, p["id"], {"reviewed"}, "out"))
        owns = len(graph.neighbors(pid, p["id"], {"owns"}, "out"))
        if wrote or reviewed or owns:
            out.append({"login": login, "wrote": wrote, "reviewed": reviewed, "owns": owns})
    out.sort(key=lambda x: -(x["wrote"] * 2 + x["reviewed"] + x["owns"]))
    return out[:40]


class ImportIn(BaseModel):
    logins: list[str]
    role: str = "engineer"


@router.post("/projects/{pid}/team/import")
def team_import(pid: str, body: ImportIn, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, LEAD)
    if body.role not in READ_ALL:
        raise HTTPException(400, "Unknown role")
    added = 0
    for login in body.logins:
        existing = db.one("SELECT id FROM users WHERE lower(github)=lower(?) AND org=?", (login, u["org"]))
        uid = existing["id"] if existing else db.uid("usr")
        if not existing:
            # An invite: they join as this person when they first sign in with that GitHub account.
            db.x("INSERT INTO users(id, org, name, email, title, org_role, shift, hours_today, client_facing, github, invited_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (uid, u["org"], login, None, "From GitHub", "member", "on", 0, 0, login, u["id"]))
        db.x("INSERT OR IGNORE INTO members(project, user, role) VALUES(?,?,?)", (pid, uid, body.role))
        added += 1
    db.event(pid, None, u["id"], "team_imported", {"count": added})
    return {"added": added}


class GithubIn(BaseModel):
    github: str


@router.post("/projects/{pid}/members/{uid}/github")
def link_github(pid: str, uid: str, body: GithubIn, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, LEAD)
    # Only for people who haven't signed in yet: a signed-in person's handle comes from GitHub itself.
    if not db.one("SELECT 1 FROM users WHERE id=? AND org=? AND github_id IS NULL", (uid, u["org"])):
        raise HTTPException(400, "Their GitHub handle comes from their sign-in")
    db.x("UPDATE users SET github=? WHERE id=?", (body.github.strip().lstrip("@") or None, uid))
    return {"ok": True}


# ---------------- GitHub issues as bugs ----------------

BUGGY = {"bug", "defect", "regression", "incident", "type: bug", "kind/bug"}


@router.post("/releases/{rid}/import_issues")
def import_issues(rid: str, x_user: str | None = Header(None)):
    from .releases import create_bug
    u = me(x_user)
    r = rel_or_404(rid)
    need(u, r["project"], TEST)
    imported = {l for b in db.q("SELECT links FROM bugs WHERE project=?", (r["project"],)) for l in json.loads(b["links"] or "[]")}
    n = 0
    for c in db.q("SELECT text, meta FROM cards WHERE project=? AND idx='changes'", (r["project"],)):
        m = json.loads(c["meta"])
        if m.get("type") != "issue" or m.get("state") != "open" or m.get("url") in imported:
            continue
        labels = {l.lower() for l in (m.get("labels") or "").split(",") if l}
        in_scope = (r["scope_type"] == "milestone" and m.get("milestone") == r["scope_value"]) or \
                   (r["scope_type"] == "label" and r["scope_value"].lower() in labels)
        if not (labels & BUGGY or in_scope):
            continue
        reporter = db.one("SELECT id FROM users WHERE github=?", (m.get("author"),))
        title = m.get("title") or c["text"].split(". ", 1)[0]
        body = c["text"].split(". ", 1)[1] if ". " in c["text"] else ""
        create_bug(r["project"], rid, "staging", title[:200], f"{body}\n\nFrom GitHub issue #{m.get('number')}: {m.get('url')}",
                   "major", reporter["id"] if reporter else (m.get("author") or "github"), "human", links=[m.get("url")])
        n += 1
    db.event(r["project"], rid, u["id"], "issues_imported", {"count": n})
    return {"imported": n}


# ---------------- monitoring and incidents ----------------

@router.get("/projects/{pid}/monitor")
def monitor_status(pid: str, x_user: str | None = Header(None)):
    from . import monitor, staging
    u = me(x_user)
    need(u, pid, WRITE | {"tester", "support"})
    return {"connected": staging.connected(pid, "monitor_db"), "url": staging.masked(pid, "monitor_db"), "settings": monitor.settings(pid)}


@router.post("/projects/{pid}/monitor")
def monitor_connect(pid: str, body: DbIn, x_user: str | None = Header(None)):
    from . import monitor, staging
    u = me(x_user)
    need(u, pid, LEAD)
    try:
        staging.verify_and_save(pid, body.url.strip(), "monitor_db", check=lambda a: a.perf())
        snap = monitor.snapshot(pid)
    except Exception as e:
        raise HTTPException(400, f"Couldn't read performance views: {_no_secret(str(e))[:240]}")
    return {"connected": True, "statements": len(snap["statements"]), "note": snap.get("statements_error") or snap.get("unsupported")}


class MonitorSettings(BaseModel):
    settings: dict


@router.post("/projects/{pid}/monitor/settings")
def monitor_settings(pid: str, body: MonitorSettings, x_user: str | None = Header(None)):
    from . import monitor
    u = me(x_user)
    need(u, pid, LEAD)
    monitor.save_settings(pid, body.settings)
    db.event(pid, None, u["id"], "guardrails_changed", body.settings)
    return monitor.settings(pid)


@router.post("/projects/{pid}/monitor/tick")
def monitor_tick(pid: str, x_user: str | None = Header(None)):
    from . import monitor
    u = me(x_user)
    need(u, pid, WRITE)
    return monitor.tick(pid)


def _incident_row(i: dict) -> dict:
    from .pipeline import name
    return {**{k: i[k] for k in ("id", "title", "severity", "status", "trigger", "rule", "opened_at", "resolved_at")},
            "owner": name(i["owner"]), "owner_id": i["owner"], "relay": json.loads(i["relay"] or "[]"), "opened_by": name(i["opened_by"]) or i["opened_by"]}


@router.get("/projects/{pid}/incidents")
def incidents(pid: str, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, READ_ALL)
    return [_incident_row(i) for i in db.q("SELECT * FROM incidents WHERE project=? ORDER BY status='resolved', opened_at DESC", (pid,))]


class IncidentIn(BaseModel):
    title: str
    severity: str = "high"
    notes: str = ""


@router.post("/projects/{pid}/incidents")
def incident_open(pid: str, body: IncidentIn, x_user: str | None = Header(None)):
    from . import monitor
    u = me(x_user)
    need(u, pid, WRITE | {"support", "tester"})
    iid = monitor.open_incident(pid, body.title, body.severity, "human", u["id"], evidence={"notes": body.notes})
    return {"id": iid}


@router.get("/incidents/{iid}")
def incident_room(iid: str, x_user: str | None = Header(None)):
    from . import testing
    from .harness.loop import runs_for
    u = me(x_user)
    i = db.one("SELECT * FROM incidents WHERE id=?", (iid,))
    if not i:
        raise HTTPException(404)
    role = need(u, i["project"], READ_ALL)
    ev = json.loads(i["evidence"] or "{}")
    series = db.q("SELECT at, value FROM signals WHERE project=? AND fingerprint=? AND at > ? ORDER BY at",
                  (i["project"], i["fingerprint"], i["opened_at"] - 3600)) if i["fingerprint"] else []
    ctx = ev.get("context") or {}
    diagram = None
    if ctx.get("tables") or ctx.get("code"):
        nodes = [{"id": "sym", "label": i["title"], "col": 0, "hot": True, "kind": ""}]
        edges = []
        for t in ctx["tables"][:2]:
            nodes.append({"id": f"table:{t}", "label": t + (f" ({', '.join(ctx.get('filter_columns', [])[:2])})" if ctx.get("filter_columns") else ""), "col": 1, "hot": True, "kind": ""})
            edges.append(("sym", f"table:{t}"))
        for c in ctx.get("code", [])[:3]:
            nodes.append({"id": c, "label": c.split("#")[-1] if "#" in c else c.split("/")[-1], "col": 2, "hot": c == ctx["code"][0], "kind": ""})
            edges += [(f"table:{t}", c) for t in ctx["tables"][:1]] if ctx.get("tables") else [("sym", c)]
        for p in ctx.get("prs", [])[:2]:
            nodes.append({"id": p, "label": (graph.node(i["project"], p) or {"label": p})["label"], "col": 3, "hot": p == ctx["prs"][0], "kind": ""})
            edges += [(c, p) for c in ctx.get("code", [])[:1]]
            for n in graph.neighbors(i["project"], p, {"authored", "reviewed"}, "in")[:2]:
                nodes.append({"id": n["id"], "label": n["id"].split(":", 1)[1], "col": 4, "hot": False, "kind": "person"})
                edges.append((p, n["id"]))
        diagram = {"columns": ["Symptom", "Data", "Code", "Change", "People"], "nodes": nodes,
                   "edges": [{"from": a, "to": b} for a, b in dict.fromkeys(edges)]}
    from . import monitor
    return {"incident": _incident_row(i), "role": role, "me": u["id"], "evidence": ev, "series": series, "diagram": diagram,
            "threshold": monitor.settings(i["project"])["slow_ms"] if i["rule"] == "slow_query" else None, "project": i["project"],
            "thread": testing.notes("incident", iid), "files": testing.attachments("incident", iid), "runs": runs_for("incident", iid),
            "proposal_why": i["proposal"]}


class ActIn(BaseModel):
    action: str  # confirm | dismiss | take | mitigating | resolve | approve_mitigation
    detail: str = ""


@router.post("/incidents/{iid}/act")
def incident_act(iid: str, body: ActIn, x_user: str | None = Header(None)):
    import threading as _t
    from . import monitor, testing
    from .pipeline import name
    u = me(x_user)
    i = db.one("SELECT * FROM incidents WHERE id=?", (iid,))
    role = need(u, i["project"], WRITE | {"support"})
    a = body.action
    if a == "confirm" and i["status"] == "proposed":
        db.x("UPDATE incidents SET status='open' WHERE id=?", (iid,))
        monitor.incident_relay(iid, "detected", "Monitoring agent", "agent", f"{i['title']}. War room opened by {u['name']}.")
        _t.Thread(target=monitor.diagnose, args=(i["project"], iid), daemon=True).start()
    elif a == "dismiss" and i["status"] == "proposed":
        db.x("UPDATE incidents SET status='dismissed', resolved_at=? WHERE id=?", (time.time(), iid))
        testing.note(i["project"], "incident", iid, u["name"], "human", f"Not an incident: {body.detail or 'dismissed'}. The agent keeps its cooldown for this signal.")
    elif a == "take":
        db.x("UPDATE incidents SET owner=? WHERE id=?", (u["id"], iid))
        monitor.incident_relay(iid, "owner", u["name"], "human", f"{u['name']} is on it")
    elif a == "approve_mitigation":
        if role not in LEAD and u["id"] != i["owner"]:
            raise HTTPException(403, "The owner or a lead approves mitigations")
        db.x("UPDATE incidents SET status='mitigating' WHERE id=?", (iid,))
        monitor.incident_relay(iid, "mitigating", u["name"], "human", f"Approved: {body.detail}. A person runs it; agents never do.")
    elif a == "resolve":
        db.x("UPDATE incidents SET status='resolved', resolved_at=? WHERE id=?", (time.time(), iid))
        monitor.incident_relay(iid, "resolved", u["name"], "human", body.detail or "Resolved")
    else:
        raise HTTPException(400, "Unknown or invalid action for this state")
    if i["trigger"] == "agent" and i["fingerprint"] and a in ("confirm", "dismiss", "resolve"):
        from . import jev  # a person's call is the ground truth for "was this signal real?"
        jev.settle("signal", i["fingerprint"], "real", a != "dismiss")
    db.event(i["project"], None, u["id"], f"incident_{a}", {"incident": iid, "detail": body.detail})
    return {"ok": True}


# ---------------- schema diagram ----------------

@router.get("/projects/{pid}/schema")
def schema(pid: str, x_user: str | None = Header(None)):
    from . import staging
    u = me(x_user)
    need(u, pid, WRITE | {"tester", "support"})
    live = {}
    if staging.connected(pid):
        try:
            live = staging.introspect(pid)
        except Exception:
            live = {}
    live_low = {t.lower(): t for t in live}
    failing = {}
    for t in db.q("SELECT target, result FROM tests WHERE project=? AND status='failed' AND target LIKE 'field:%'", (pid,)):
        failing[t["target"].split(":", 1)[1]] = t["result"]
    tables, relations = [], []
    for t in db.q("SELECT id, label, props FROM nodes WHERE project=? AND type='table'", (pid,)):
        props = json.loads(t["props"] or "{}")
        lt = next((live_low[c] for c in (t["label"].lower(), t["label"].lower().rstrip("s"), t["label"].lower() + "s") if c in live_low), None)
        lcols = live.get(lt, {}).get("columns", {}) if lt else {}
        lpk = live.get(lt, {}).get("pk", []) if lt else []
        cols, readers, writers = [], set(), set()
        for f in graph.neighbors(pid, t["id"], {"has_field"}, "out"):
            n = graph.node(pid, f["id"]) or {"label": f["id"], "props": {}}
            name = n["label"].split(".", 1)[1]
            r = [x["id"] for x in graph.neighbors(pid, f["id"], {"uses_field"}, "in")]
            w = [x["id"] for x in graph.neighbors(pid, f["id"], {"writes_field"}, "in")]
            readers.update(r); writers.update(w)
            lc = next((v for k, v in lcols.items() if k.lower() == name.lower()), None)
            cols.append({"name": name, "type": n["props"].get("type"), "required": n["props"].get("nullable") is False,
                         "pk": bool(n["props"].get("pk")) or name in lpk, "fk": n["props"].get("fk"), "reads": len(r), "writes": len(w),
                         "live": None if not lt else ({"present": True, "nullable": lc["nullable"], "type": lc["type"]} if lc else {"present": False}),
                         "failing": failing.get(n["label"])})
            if n["props"].get("fk"):
                relations.append({"from": f"{t['label']}.{name}", "to": n["props"]["fk"]})
        for lname in lcols:  # columns in the database the code doesn't declare
            if not any(c["name"].lower() == lname.lower() for c in cols):
                cols.append({"name": lname, "type": lcols[lname]["type"], "required": not lcols[lname]["nullable"], "pk": lname in lpk,
                             "fk": None, "reads": 0, "writes": 0, "live": {"present": True, "only_live": True}, "failing": None})
        eps = {e["id"] for s in writers | readers for e in graph.neighbors(pid, s, {"handled_by"}, "in")}
        tables.append({"name": t["label"], "source": props.get("source"), "live_name": lt, "columns": cols,
                       "readers": sorted(x.split("#")[-1] for x in readers)[:12], "writers": sorted(x.split("#")[-1] for x in writers)[:12],
                       "endpoints": sorted((graph.node(pid, e) or {"label": e})["label"].split("@")[0] for e in eps)[:10]})
    if live and staging.connected(pid):  # foreign keys that exist only in the database
        try:
            for fk in staging.foreign_keys(pid):
                rel = {**fk, "live": True}
                if not any(r["from"].lower() == rel["from"].lower() for r in relations):
                    relations.append(rel)
        except Exception:
            pass
    code_names = {t["name"].lower() for t in tables} | {(t["live_name"] or "").lower() for t in tables}
    only_live = [t for t in live if t.lower() not in code_names]
    return {"tables": tables, "relations": relations, "live_connected": bool(live), "only_live": only_live[:30]}


# ---------------- context map and explorer ----------------

@router.get("/projects/{pid}/context/map")
def context_map(pid: str, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, WRITE | {"tester", "support"})
    counts = {r["type"]: r["n"] for r in db.q("SELECT type, count(*) n FROM nodes WHERE project=? GROUP BY type", (pid,))}
    links = db.q("""SELECT s.type AS a, d.type AS b, e.type AS edge, count(*) n FROM edges e
                    JOIN nodes s ON s.project=e.project AND s.id=e.src JOIN nodes d ON d.project=e.project AND d.id=e.dst
                    WHERE e.project=? AND e.valid_to IS NULL GROUP BY s.type, d.type, e.type""", (pid,))
    cards = {}
    for c in db.q("SELECT meta FROM cards WHERE project=?", (pid,)):
        t = json.loads(c["meta"]).get("type", "other")
        cards[t] = cards.get(t, 0) + 1
    extra = {"incident": db.one("SELECT count(*) n FROM incidents WHERE project=?", (pid,))["n"],
             "bug": db.one("SELECT count(*) n FROM bugs WHERE project=?", (pid,))["n"],
             "decision": db.one("SELECT count(*) n FROM decisions WHERE project=?", (pid,))["n"]}
    return {"nodes": counts, "links": links, "cards": cards, "work": extra}


@router.get("/projects/{pid}/context/ask")
def context_ask(pid: str, q: str, x_user: str | None = Header(None)):
    """Search for people: a plain answer, readable grouped results, and whether anything really matched."""
    from . import ask
    u = me(x_user)
    need(u, pid, WRITE | {"tester", "support"})
    return ask.ask(pid, q.strip()[:200], u["id"])


@router.get("/projects/{pid}/context/areas")
def context_areas(pid: str, x_user: str | None = Header(None)):
    """The project as areas people talk about, each traced from requests to people. Drives the context map."""
    from . import areas, tracks
    u = me(x_user)
    need(u, pid, WRITE | {"tester", "support"})
    out = areas.build(pid)
    for a in out["areas"]:
        t = tracks.owner_of_area(pid, a["id"])
        a["track"] = {"id": t["id"], "name": t["name"]} if t else None
    return out


EDGE_WORDS = {"defines": ("defines", "defined in"), "calls": ("calls", "called by"), "imports": ("imports", "imported by"),
              "uses_field": ("reads", "read by"), "writes_field": ("writes", "written by"), "handled_by": ("handled by", "serves"),
              "touches": ("changed", "changed by"), "authored": ("wrote", "written by"), "reviewed": ("reviewed", "reviewed by"),
              "owns": ("owns", "owned by"), "has_field": ("has column", "column of"), "references": ("references", "referenced by"),
              "alters": ("alters", "altered by"), "co_changes": ("changes with", "changes with")}


@router.get("/projects/{pid}/graph/node")
def graph_node(pid: str, id: str, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, WRITE | {"tester", "support"})
    from .memory import memory_for
    n = graph.node(pid, id)
    card = memory_for(pid).get(id)
    if not n and not card:
        raise HTTPException(404)
    groups: dict[str, list] = {}
    for nb in graph.neighbors(pid, id):
        word = EDGE_WORDS.get(nb["edge"], (nb["edge"], nb["edge"]))[0 if nb["dir"] == "out" else 1]
        other = graph.node(pid, nb["id"]) or {"type": nb["id"].split(":")[0], "label": nb["id"]}
        key = f"{nb['dir']}|{word}|{other['type']}"
        groups.setdefault(key, []).append({"id": nb["id"], "label": other["label"].split("@")[0], "type": other["type"]})
    out = [{"dir": k.split("|")[0], "relation": k.split("|")[1], "type": k.split("|")[2], "count": len(v), "items": v[:12]}
           for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
    return {"node": {"id": id, "type": (n or {}).get("type") or id.split(":")[0], "label": (n or {}).get("label") or id,
                     "props": (n or {}).get("props") or {}, "text": card["text"][:600] if card else None}, "groups": out}


@router.get("/projects/{pid}/graph/list")
def graph_list(pid: str, type: str, x_user: str | None = Header(None)):
    u = me(x_user)
    need(u, pid, WRITE | {"tester", "support"})
    rows = db.q("""SELECT n.id, n.label, (SELECT count(*) FROM edges e WHERE e.project=n.project AND (e.src=n.id OR e.dst=n.id) AND e.valid_to IS NULL) AS degree
                   FROM nodes n WHERE n.project=? AND n.type=? ORDER BY degree DESC LIMIT 30""", (pid, type))
    return [{"id": r["id"], "label": r["label"].split("@")[0], "degree": r["degree"]} for r in rows]



# ---------------- log and error sources ----------------

@router.get("/projects/{pid}/sources")
def sources_list(pid: str, x_user: str | None = Header(None)):
    from . import sources
    u = me(x_user)
    need(u, pid, WRITE | {"tester", "support"})
    return {"sources": sources.listing(pid), "kinds": sources.KINDS,
            "patterns": db.q("SELECT fingerprint, template, total, last_seen, source FROM patterns WHERE project=? ORDER BY last_seen DESC LIMIT 20", (pid,))}


class SourceIn(BaseModel):
    kind: str
    name: str = ""
    config: dict = {}
    secret: str | None = None


@router.post("/projects/{pid}/sources")
def sources_add(pid: str, body: SourceIn, x_user: str | None = Header(None)):
    from . import sources
    u = me(x_user)
    need(u, pid, LEAD)
    if body.kind not in sources.KINDS:
        raise HTTPException(400, "Unknown source")
    sid = sources.add(pid, body.kind, body.name, body.config, body.secret)
    try:
        window = sources.poll(pid)
    except Exception as e:
        window = []
        db.x("UPDATE sources SET status=? WHERE id=?", (f"error: {str(e)[:160]}", sid))
    src = next(s for s in sources.listing(pid) if s["id"] == sid)
    if src["status"].startswith("error"):
        sources.remove(pid, sid)
        raise HTTPException(400, src["status"])
    return {"id": sid, "status": src["status"], "patterns": len(window)}


@router.delete("/projects/{pid}/sources/{sid}")
def sources_remove(pid: str, sid: str, x_user: str | None = Header(None)):
    from . import sources
    u = me(x_user)
    need(u, pid, LEAD)
    sources.remove(pid, sid)
    return {"ok": True}


# ---------------- Now / Setup / live feed ----------------

def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


@router.get("/projects/{pid}/now")
def project_now(pid: str, x_user: str | None = Header(None)):
    from .pipeline import HOURS_CAP, load, name
    u = me(x_user)
    role = need(u, pid, READ_ALL)
    needs = []
    for i in db.q("SELECT * FROM incidents WHERE project=? AND status IN ('proposed','open','mitigating') ORDER BY opened_at DESC", (pid,)):
        if i["status"] == "proposed":
            needs.append({"kind": "incident_proposed", "id": i["id"], "context": f"War room, proposed by the monitoring agent at {time.strftime('%H:%M', time.localtime(i['opened_at']))}",
                          "title": i["title"], "action": "Open war room"})
        elif not i["owner"] or i["owner"] == u["id"]:
            needs.append({"kind": "incident_open", "id": i["id"], "context": f"War room open since {time.strftime('%H:%M', time.localtime(i['opened_at']))}",
                          "title": i["title"], "action": "Go to war room"})
    rel = db.one("SELECT * FROM releases WHERE project=? AND stage NOT IN ('closed') ORDER BY created DESC", (pid,))
    stats = None
    if rel:
        proposed = db.q("SELECT id, proposed, eta_agent FROM bugs WHERE release=? AND stage='proposed'", (rel["id"],))
        mine = [b for b in proposed if b["proposed"] == u["id"]]
        if (role in ("owner", "lead") and proposed) or mine:
            who = sorted({name(b["proposed"]) for b in (proposed if role in ("owner", "lead") else mine) if b["proposed"]})
            needs.append({"kind": "owners", "id": rel["id"], "context": f"{rel['name']} release, {rel['stage'].replace('_', ' ')}",
                          "title": f"{_plural(len(proposed if role in ('owner', 'lead') else mine), 'bug has', 'bugs have')} a proposed owner. Approve to start fixing.",
                          "detail": ", ".join(who), "action": "Review owners"})
        asks = db.one("SELECT count(*) n FROM tests WHERE release=? AND claimed_by=? AND status IN ('todo','running')", (rel["id"], u["id"]))["n"]
        if asks:
            needs.append({"kind": "tests", "id": rel["id"], "context": f"{rel['name']} release, testing", "title": f"The agent asked you to run {_plural(asks, 'check')}.", "action": "Open checks"})
        high = db.q("SELECT status FROM risks WHERE release=? AND level='high'", (rel["id"],))
        stats = {"id": rel["id"], "name": rel["name"], "stage": rel["stage"], "window": rel["window_start"],
                 "waiting_owner": len(proposed), "fixing": db.one("SELECT count(*) n FROM bugs WHERE release=? AND stage='fixing'", (rel["id"],))["n"],
                 "agents_on": db.one("SELECT count(*) n FROM bugs WHERE release=? AND stage IN ('reported','reproduced','cause_found')", (rel["id"],))["n"],
                 "verify": db.one("SELECT count(*) n FROM bugs WHERE release=? AND stage='fixed'", (rel["id"],))["n"],
                 "risks_tested": sum(1 for h in high if h["status"] == "verified"), "risks_high": len(high),
                 "queue": [{"id": b["id"], "title": b["title"], "hours": b["eta_agent"] or 2, "proposed": name(b["proposed"]) if b["proposed"] else None, "proposed_id": b["proposed"]}
                           for b in db.q("SELECT id, title, eta_agent, proposed FROM bugs WHERE release=? AND stage='proposed' ORDER BY created", (rel["id"],))]}
    busy = load(pid)
    team = []
    for m in db.q("""SELECT u.id, u.name, u.shift, u.hours_today FROM members m JOIN users u ON u.id=m.user
                     WHERE m.project=? AND m.role IN ('engineer','lead','owner')""", (pid,)):
        agreed = sum(b["eta_owner"] or b["eta_agent"] or 0 for b in db.q("SELECT eta_owner, eta_agent FROM bugs WHERE project=? AND stage='fixing' AND assignee=?", (pid, m["id"])))
        prop = busy.get(m["id"], 0) - agreed
        team.append({"id": m["id"], "name": m["name"], "shift": m["shift"], "worked": m["hours_today"], "agreed": round(agreed, 1),
                     "proposed": round(max(prop, 0), 1), "cap": HOURS_CAP})
    repo = db.one("SELECT synced FROM repos WHERE project=? ORDER BY synced DESC", (pid,))
    counts = {r["type"]: r["n"] for r in db.q("SELECT type, count(*) n FROM nodes WHERE project=? AND type IN ('pr','file','person') GROUP BY type", (pid,))}
    open_wr = sum(1 for n in needs if n["kind"].startswith("incident"))
    parts = []
    if open_wr:
        parts.append(f"{_plural(open_wr, 'war room')} need{'s' if open_wr == 1 else ''} you.")
    if stats:
        bits = [f"{stats['waiting_owner']} waiting for an owner" if stats["waiting_owner"] else "", f"{stats['fixing']} being fixed" if stats["fixing"] else "",
                f"agents working on {stats['agents_on']}" if stats["agents_on"] else ""]
        bits = [b for b in bits if b]
        parts.append(f"The {stats['name']} release is on {stats['stage'].replace('_', ' ')}" + (f": {', '.join(bits)}." if bits else ", nothing open."))
    if not parts:
        parts.append("All quiet. Agents are watching production and the next release.")
    return {"sentence": " ".join(parts), "needs": needs, "release": stats, "team": team, "role": role,
            "synced": repo["synced"] if repo else None, "counts": counts, "setup_todo": len([s for s in _setup_items(pid) if not s["done"]])}


def _setup_items(pid: str) -> list[dict]:
    from . import staging
    p = db.one("SELECT * FROM projects WHERE id=?", (pid,))
    repo = db.one("SELECT * FROM repos WHERE project=? ORDER BY synced DESC", (pid,))
    counts = {r["type"]: r["n"] for r in db.q("SELECT type, count(*) n FROM nodes WHERE project=? GROUP BY type", (pid,))}
    owners = db.one("SELECT count(*) n FROM edges WHERE project=? AND type='owns'", (pid,))["n"]
    members = db.one("SELECT count(*) n FROM members WHERE project=?", (pid,))["n"]
    sources_n = db.one("SELECT count(*) n FROM sources WHERE project=?", (pid,))["n"] if db.one("SELECT name FROM sqlite_master WHERE name='sources'") else 0
    return [
        {"id": "repo", "done": bool(repo and repo["status"] == "ready"), "title": "Repo synced",
         "detail": f"{counts.get('file', 0)} files, {counts.get('endpoint', 0)} endpoints, {counts.get('pr', 0)} PRs." if repo else "Connect a GitHub repo.",
         "why": "Everything else builds on the code graph."},
        {"id": "people", "done": members > 1, "title": "People added", "detail": f"{members} on this project, {counts.get('person', 0)} seen in the repo.",
         "why": "The dispatcher routes fixes to people who wrote the code."},
        {"id": "staging_db", "done": staging.connected(pid), "title": "Connect the staging database",
         "detail": staging.masked(pid) or "", "why": "Read-only. Lets agents check data and reproduce bugs by querying real rows."},
        {"id": "monitor_db", "done": staging.connected(pid, "monitor_db"), "title": "Connect production monitoring",
         "detail": staging.masked(pid, "monitor_db") or "", "why": "Read-only query stats. The monitoring agent opens war rooms when something crosses your guardrails."},
        {"id": "sources", "done": sources_n > 0, "title": "Connect logs or errors", "detail": _plural(sources_n, "source") if sources_n else "",
         "why": "Sentry, Datadog, Grafana Loki or a log file. Errors link to the code through their stack traces."},
        {"id": "codeowners", "done": owners > 0, "title": "Add a CODEOWNERS file", "detail": "",
         "why": "Owners come from PR history only right now, so routing is a guess for code nobody touched recently."},
        {"id": "staging_url", "done": bool(p["staging_url"]), "title": "Set the staging URL", "detail": p["staging_url"] or "",
         "why": "The agent tester calls safe GET endpoints there."},
        _brief_item(pid),
    ]


def _brief_item(pid: str) -> dict:
    from . import brief
    done, total = brief.progress(pid)
    return {"id": "brief", "done": done >= min(6, total), "title": "Tell agents how the product is used",
            "detail": f"{done} of {total} answered" if done else "",
            "why": "Code shows what exists, not what matters: critical flows, peak hours, what counts as broken, rules that live in people's heads."}


@router.get("/projects/{pid}/setup")
def project_setup(pid: str, x_user: str | None = Header(None)):
    u = me(x_user)
    role = need(u, pid, READ_ALL)
    items = _setup_items(pid)
    done = sum(1 for i in items if i["done"])
    ready = {i["id"] for i in items if i["done"]}
    can = ["find causes"] if "repo" in ready else []
    if "staging_db" in ready:
        can.append("check data")
    if "people" in ready:
        can.append("route to owners")
    if "monitor_db" in ready or "sources" in ready:
        can.append("open war rooms")
    sentence = f"{done} of {len(items)} done." + (f" Agents can {', '.join(can)}." if can else "") + \
        (" Finish the rest and they can do more." if done < len(items) else " Everything is connected.")
    return {"sentence": sentence, "items": items, "role": role}


@router.get("/projects/{pid}/codeowners/draft")
def codeowners_draft(pid: str, x_user: str | None = Header(None)):
    """Top authors per top-level folder, from PR history. A draft for a person to review and commit."""
    u = me(x_user)
    need(u, pid, LEAD)
    dirs: dict[str, dict[str, int]] = {}
    for e in db.q("""SELECT a.src AS person, t.dst AS file FROM edges a JOIN edges t ON t.project=a.project AND t.src=a.dst
                     WHERE a.project=? AND a.type='authored' AND t.type='touches'""", (pid,)):
        path = e["file"].split(":", 2)[-1]
        parts = path.split("/")
        if len(parts) < 2 or parts[0].startswith("."):
            continue  # root files and tool folders: too noisy to own
        top = "/".join(parts[:2]) if len(parts) > 2 else parts[0]
        login = e["person"].split(":", 1)[1]
        if login.endswith("[bot]"):
            continue
        dirs.setdefault(top, {})[login] = dirs.setdefault(top, {}).get(login, 0) + 1
    lines = ["# Drafted by War Room OS from PR history. Review before committing."]
    for d, people in sorted(dirs.items()):
        if sum(people.values()) < 2:
            continue
        top = [f"@{p}" for p, _ in sorted(people.items(), key=lambda kv: -kv[1])[:2]]
        lines.append(f"/{d}/ {' '.join(top)}")
    return {"text": "\n".join(lines)}


EVENT_WORDS = {
    "bug_reported": "reported a bug", "bug_reproduced": "reproduced a bug", "bug_cause_found": "found a cause", "bug_proposed": "proposed an owner",
    "bug_fixing": "took a fix", "bug_fixed": "fixed a bug", "bug_verified": "verified a fix", "incident_opened": "opened a war room",
    "incident_proposed": "proposed a war room", "incident_take": "took a war room", "incident_resolve": "resolved a war room",
    "incident_approve_mitigation": "approved a mitigation", "test_finished": "finished a check", "plan": "planned checks", "run": "ran checks",
    "stage": "moved the release", "decision": "pinned a decision", "repo_synced": "synced the repo", "premortem": "updated the pre-mortem",
    "team_imported": "added people", "issues_imported": "imported GitHub issues", "experts_built": "built expert agents",
    "expert_taught": "taught an expert", "signal_held": "held back a signal as likely noise", "brief_answered": "answered a question about the project", "watch_started": "started watching a release",
    "watch_done": "finished watching a release", "handbook_added": "added to the team handbook", "explorer_started": "started exploring staging",
    "explorer_done": "finished exploring staging", "track_created": "created a track",
    "routed_to_track": "routed work to a team",
}


@router.get("/projects/{pid}/feed")
def feed(pid: str, after: int = 0, x_user: str | None = Header(None)):
    from .pipeline import name
    u = me(x_user)
    need(u, pid, READ_ALL)
    rows = db.q("SELECT seq, ts, actor, kind, data FROM events WHERE project=? AND seq>? ORDER BY seq DESC LIMIT 8", (pid, after))
    out = []
    for r in rows:
        if r["kind"] not in EVENT_WORDS:
            continue
        d = json.loads(r["data"] or "{}")
        actor = name(r["actor"]) or r["actor"]
        agent = any(w in actor.lower() for w in ("agent", "dispatcher", "monitor", "context")) or r["actor"] in ("agent-tester", "risk-agent", "monitor", "context")
        what = d.get("title") or d.get("note") or ""
        out.append({"seq": r["seq"], "ts": r["ts"], "actor": {"agent-tester": "Agent tester", "risk-agent": "Risk agent", "monitor": "Monitoring agent", "context": "Context agent"}.get(r["actor"], actor),
                    "agent": agent, "text": EVENT_WORDS[r["kind"]], "detail": str(what)[:90]})
    return out



# ---------------- expert agents ----------------

@router.get("/projects/{pid}/experts")
def experts_list(pid: str, x_user: str | None = Header(None)):
    from . import experts
    u = me(x_user)
    need(u, pid, READ_ALL)
    levels = db.q("""SELECT u.id, u.name, m.role, m.level FROM members m JOIN users u ON u.id=m.user
                     WHERE m.project=? AND m.role IN ('engineer','lead','owner') ORDER BY u.name""", (pid,))
    return {"experts": experts.listing(pid), "levels": levels, "choices": experts.LEVELS}


@router.post("/projects/{pid}/experts/build")
def experts_build(pid: str, x_user: str | None = Header(None)):
    from . import experts
    u = me(x_user)
    need(u, pid, LEAD)
    return {"built": experts.build(pid)}


class TeachIn(BaseModel):
    text: str


@router.post("/experts/{eid}/teach")
def experts_teach(eid: str, body: TeachIn, x_user: str | None = Header(None)):
    from . import experts
    u = me(x_user)
    e = db.one("SELECT project FROM experts WHERE id=?", (eid,))
    if not e:
        raise HTTPException(404, "No such expert")
    need(u, e["project"], WRITE)
    if len(body.text.strip()) < 15:
        raise HTTPException(400, "Write the lesson as a full sentence.")
    return {"id": experts.teach(e["project"], eid, body.text, u.get("github") or u["name"])}


@router.post("/bugs/{bid}/expert")
def bug_expert(bid: str, x_user: str | None = Header(None)):
    from . import pipeline
    u = me(x_user)
    b = _bug(bid)
    need(u, b["project"], TEST | {"support"})
    r = pipeline.expert_review(b["project"], bid, b["assignee"] or b["proposed"], force=True)
    if not r:
        raise HTTPException(400, "No expert covers this code yet. Build experts in Setup after the repo syncs.")
    return {"ok": True}


class VerdictIn(BaseModel):
    verdict: str


@router.post("/advice/{aid}/verdict")
def advice_verdict(aid: str, body: VerdictIn, x_user: str | None = Header(None)):
    from . import experts
    u = me(x_user)
    a = db.one("SELECT project FROM advice WHERE id=?", (aid,))
    if not a or body.verdict not in ("followed", "not_useful", "signed_off"):
        raise HTTPException(400, "Bad verdict")
    need(u, a["project"], WRITE)
    experts.verdict(aid, body.verdict, u["id"])
    return {"ok": True}


class LevelIn(BaseModel):
    level: str | None = None


@router.post("/projects/{pid}/members/{uid_}/level")
def member_level(pid: str, uid_: str, body: LevelIn, x_user: str | None = Header(None)):
    from . import experts
    u = me(x_user)
    need(u, pid, LEAD)
    if body.level not in (None, *experts.LEVELS):
        raise HTTPException(400, "Unknown level")
    db.x("UPDATE members SET level=? WHERE project=? AND user=?", (body.level, pid, uid_))
    return {"ok": True}



# ---------------- Jev ----------------

@router.get("/projects/{pid}/jev")
def jev_status(pid: str, x_user: str | None = Header(None)):
    """Is Jev on, what it costs so far, and how well its probabilities have matched what people decided."""
    from . import jev
    u = me(x_user)
    need(u, pid, READ_ALL)
    recent = db.q("""SELECT subject_type, subject_id, purpose, question, p, outcome, at FROM jev_decisions
                     WHERE project=? ORDER BY at DESC LIMIT 20""", (pid,))
    return {**jev.status(), "calibration": jev.calibration(pid), "recent": recent}



# ---------------- project brief ----------------

@router.get("/projects/{pid}/brief")
def brief_list(pid: str, x_user: str | None = Header(None)):
    from . import brief
    u = me(x_user)
    role = need(u, pid, READ_ALL)
    rows = brief.listing(pid)
    return {"questions": rows, "answered": sum(1 for r in rows if r["answer"]), "can_answer": role in WRITE | {"support"}}


class BriefIn(BaseModel):
    answer: str = ""


@router.post("/projects/{pid}/brief/{qid}")
def brief_answer(pid: str, qid: str, body: BriefIn, x_user: str | None = Header(None)):
    from . import brief
    u = me(x_user)
    need(u, pid, WRITE | {"support"})
    try:
        brief.answer(pid, qid, body.answer[:4000], u["name"])
    except KeyError:
        raise HTTPException(404, "No such question")
    db.event(pid, None, u["id"], "brief_answered", {"note": qid})
    return {"ok": True}



# ---------------- watch after release ----------------

@router.get("/releases/{rid}/watch")
def watch_get(rid: str, x_user: str | None = Header(None)):
    from . import watch
    u = me(x_user)
    r = db.one("SELECT * FROM releases WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "No such release")
    need(u, r["project"], READ_ALL)
    w = watch.get(r)
    if w["status"] == "watching":  # live numbers for each watched query
        tables = [t["id"] for t in w["targets"] if t.get("on", True) and t["kind"] == "table"]
        now = watch._statements_for(r["project"], tables, max(w["started_at"], time.time() - 1800))
        w["compare"] = [{"query": b["query"][:120], "before_ms": b["ms"], "now_ms": now.get(fp, {}).get("ms")}
                        for fp, b in w["baseline"].get("queries", {}).items()]
    w.pop("baseline", None) if w["status"] != "done" else None
    w["monitoring"] = __import__("backend.staging", fromlist=["x"]).connected(r["project"], "monitor_db") or \
        bool(db.one("SELECT 1 FROM sources WHERE project=?", (r["project"],)))
    return w


class WatchIn(BaseModel):
    hours: float = 24
    targets: list[dict] = []
    settings: dict = {}


@router.post("/releases/{rid}/watch")
def watch_set(rid: str, body: WatchIn, x_user: str | None = Header(None)):
    from . import watch
    u = me(x_user)
    r = db.one("SELECT * FROM releases WHERE id=?", (rid,))
    need(u, r["project"], LEAD)
    w = watch.configure(r, body.hours, body.targets, body.settings)
    if r["stage"] == "live" and w["status"] == "scheduled":
        w = watch.start(r)
    db.event(r["project"], rid, u["id"], "watch_configured", {"note": f"{body.hours:g} h, {sum(1 for t in body.targets if t.get('on', True))} things"})
    return w


@router.post("/releases/{rid}/watch/stop")
def watch_stop(rid: str, x_user: str | None = Header(None)):
    from . import watch
    u = me(x_user)
    r = db.one("SELECT * FROM releases WHERE id=?", (rid,))
    need(u, r["project"], LEAD)
    return watch.finish(r, f"stopped early by {u['name']}")



# ---------------- org experts ----------------

def _org_admin(u: dict):
    if u["org_role"] != "admin":
        raise HTTPException(403, "Only org admins manage team knowledge")


@router.get("/org/knowledge")
def org_knowledge(x_user: str | None = Header(None)):
    from . import org
    u = me(x_user)
    return {**org.listing(u["org"]), "can_manage": u["org_role"] == "admin"}


@router.post("/org/handbook")
async def org_handbook(file: UploadFile = File(...), x_user: str | None = Header(None)):
    from . import org
    u = me(x_user)
    _org_admin(u)
    name = file.filename or "handbook.md"
    if not re.search(r"\.(md|markdown|txt|pdf)$", name, re.I):
        raise HTTPException(400, "Upload Markdown, text or PDF")
    data = await file.read()
    if len(data) > 15_000_000:
        raise HTTPException(400, "Keep it under 15 MB")
    out = org.add_handbook(u["org"], name, data, u["name"])
    db.event(None, None, u["id"], "handbook_added", {"note": f"{name}: {out['sections']} sections"})
    return out


@router.delete("/org/handbook/{did}")
def org_handbook_delete(did: str, x_user: str | None = Header(None)):
    from . import org
    u = me(x_user)
    _org_admin(u)
    org.remove_handbook(u["org"], did)
    return {"ok": True}


class PromoteIn(BaseModel):
    topic: str


@router.post("/lessons/{lid}/promote")
def lesson_promote(lid: str, body: PromoteIn, x_user: str | None = Header(None)):
    from . import org
    u = me(x_user)
    les = db.one("SELECT project FROM lessons WHERE id=?", (lid,))
    if not les:
        raise HTTPException(404, "No such lesson")
    need(u, les["project"], LEAD)
    if body.topic not in org.TOPICS and body.topic != "general":
        raise HTTPException(400, "Unknown topic")
    return org.propose(u["org"], lid, body.topic, u["name"])


class DecideIn(BaseModel):
    approve: bool
    text: str | None = None


@router.post("/org/lessons/{oid}/decide")
def org_decide(oid: str, body: DecideIn, x_user: str | None = Header(None)):
    from . import org
    u = me(x_user)
    _org_admin(u)
    org.decide(u["org"], oid, body.approve, u["name"], body.text)
    return {"ok": True}


class OrgTeachIn(BaseModel):
    topic: str
    text: str


@router.post("/org/lessons")
def org_teach(body: OrgTeachIn, x_user: str | None = Header(None)):
    from . import org
    u = me(x_user)
    _org_admin(u)
    if len(body.text.strip()) < 15:
        raise HTTPException(400, "Write the practice as a full sentence.")
    return {"id": org.teach(u["org"], body.topic, body.text, u["name"])}


@router.get("/projects/{pid}/org-experts")
def project_org_experts(pid: str, x_user: str | None = Header(None)):
    from . import org
    u = me(x_user)
    need(u, pid, READ_ALL)
    return {"experts": org.experts_for(pid)}



# ---------------- API explorer (staging) ----------------

@router.post("/projects/{pid}/explorer")
def explorer_start(pid: str, x_user: str | None = Header(None)):
    from . import explorer
    u = me(x_user)
    need(u, pid, LEAD)
    try:
        return {"run": explorer.start(pid, u["id"])}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@router.get("/projects/{pid}/explorer")
def explorer_latest(pid: str, x_user: str | None = Header(None)):
    from . import explorer
    u = me(x_user)
    need(u, pid, WRITE | {"tester", "support"})
    p = db.one("SELECT staging_url, settings FROM projects WHERE id=?", (pid,))
    return {"run": explorer.latest(pid), "staging_url": p["staging_url"], "writes": bool(json.loads(p["settings"] or "{}").get("explorer_writes"))}



# ---------------- tracks ----------------

def _track(tid: str) -> dict:
    t = db.one("SELECT * FROM tracks WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404, "No such track")
    return t


@router.get("/projects/{pid}/tracks")
def tracks_list(pid: str, x_user: str | None = Header(None)):
    from . import areas, tracks
    u = me(x_user)
    role = need(u, pid, READ_ALL)
    return {"tracks": tracks.listing(pid), "mine": tracks.of_user(pid, u["id"]), "can_manage": role in LEAD,
            "areas": [{"id": a["id"], "name": a["name"]} for a in areas.build(pid)["areas"]],
            "members": db.q("SELECT u.id, u.name FROM members m JOIN users u ON u.id=m.user WHERE m.project=? ORDER BY u.name", (pid,))}


class TrackIn(BaseModel):
    name: str
    kind: str = "engineering"
    about: str = ""


@router.post("/projects/{pid}/tracks")
def tracks_create(pid: str, body: TrackIn, x_user: str | None = Header(None)):
    from . import tracks
    u = me(x_user)
    need(u, pid, LEAD)
    if not body.name.strip():
        raise HTTPException(400, "Name the track")
    tid = tracks.create(pid, body.name, body.kind, body.about, u["id"])
    db.event(pid, None, u["id"], "track_created", {"note": body.name})
    return {"id": tid}


class TrackPatch(BaseModel):
    name: str | None = None
    about: str | None = None
    members: list[str] | None = None
    areas: list[str] | None = None


@router.post("/tracks/{tid}")
def tracks_update(tid: str, body: TrackPatch, x_user: str | None = Header(None)):
    from . import tracks
    u = me(x_user)
    t = _track(tid)
    need(u, t["project"], LEAD)
    if body.name is not None or body.about is not None:
        db.x("UPDATE tracks SET name=coalesce(?, name), about=coalesce(?, about) WHERE id=?", (body.name, body.about, tid))
    if body.members is not None:
        tracks.set_members(tid, body.members)
    if body.areas is not None:
        tracks.set_areas(tid, body.areas)
    return {"ok": True}


@router.delete("/tracks/{tid}")
def tracks_delete(tid: str, x_user: str | None = Header(None)):
    u = me(x_user)
    t = _track(tid)
    need(u, t["project"], LEAD)
    for table in ("track_members", "track_areas", "track_notes"):
        db.x(f"DELETE FROM {table} WHERE track=?", (tid,))
    db.x("DELETE FROM brief WHERE project=? AND qid LIKE ?", (t["project"], f"{tid}/%"))
    db.x("DELETE FROM cards WHERE project=? AND meta LIKE ?", (t["project"], f'%"track": "{tid}"%'))
    db.x("UPDATE bugs SET track=NULL WHERE track=?", (tid,))
    db.x("UPDATE incidents SET track=NULL WHERE track=?", (tid,))
    db.x("DELETE FROM tracks WHERE id=?", (tid,))
    return {"ok": True}


@router.get("/tracks/{tid}")
def tracks_get(tid: str, x_user: str | None = Header(None)):
    from . import tracks
    u = me(x_user)
    t = _track(tid)
    role = need(u, t["project"], READ_ALL)
    mine = tid in tracks.of_user(t["project"], u["id"])
    return {**tracks.status(t["project"], tid), "questions": tracks.questions(t["project"], tid), "mine": mine,
            "can_edit": mine or role in LEAD, "project": db.one("SELECT id, name FROM projects WHERE id=?", (t["project"],))}


@router.post("/tracks/{tid}/brief/{qid}")
def tracks_answer(tid: str, qid: str, body: BriefIn, x_user: str | None = Header(None)):
    from . import tracks
    u = me(x_user)
    t = _track(tid)
    role = need(u, t["project"], READ_ALL)
    if tid not in tracks.of_user(t["project"], u["id"]) and role not in LEAD:
        raise HTTPException(403, "Only this team and leads answer its questions")
    tracks.answer(t["project"], tid, qid, body.answer[:4000], u["name"])
    return {"ok": True}


class NoteIn(BaseModel):
    title: str = ""
    text: str


@router.post("/tracks/{tid}/notes")
def tracks_note(tid: str, body: NoteIn, x_user: str | None = Header(None)):
    from . import tracks
    u = me(x_user)
    t = _track(tid)
    role = need(u, t["project"], READ_ALL)
    if tid not in tracks.of_user(t["project"], u["id"]) and role not in LEAD:
        raise HTTPException(403, "Only this team and leads add its notes")
    if len(body.text.strip()) < 20:
        raise HTTPException(400, "Paste the runbook, decision or glossary")
    return {"id": tracks.add_note(t["project"], tid, body.title, body.text[:60000], u["name"])}


@router.delete("/tracks/{tid}/notes/{nid}")
def tracks_note_delete(tid: str, nid: str, x_user: str | None = Header(None)):
    from . import tracks
    u = me(x_user)
    t = _track(tid)
    need(u, t["project"], LEAD)
    tracks.remove_note(t["project"], tid, nid)
    return {"ok": True}


@router.get("/tracks/{tid}/incidents/{iid}/update")
def tracks_client_update(tid: str, iid: str, x_user: str | None = Header(None)):
    from . import tracks
    u = me(x_user)
    t = _track(tid)
    need(u, t["project"], READ_ALL)
    return {"text": tracks.client_update(t["project"], tid, iid)}


@router.get("/releases/{rid}/tracks")
def release_tracks(rid: str, x_user: str | None = Header(None)):
    from . import tracks
    u = me(x_user)
    r = db.one("SELECT * FROM releases WHERE id=?", (rid,))
    need(u, r["project"], READ_ALL)
    return {"tracks": tracks.affected_by_release(r)}
