"""Bug relay: agents do the legwork, people make the calls.

reported -> reproduced -> cause_found -> proposed -> fixing -> fixed -> verified
            (repro agent)  (root cause)   (dispatcher  (owner accepts,  (owner)  (tester)
                                           + ETA agent)  agrees ETA)

Every step is written to the bug's relay with who did it (agent or person), so the UI can show it.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time

import httpx

from . import db, graph

STAGES = ["reported", "reproduced", "cause_found", "proposed", "fixing", "fixed", "verified"]
HOURS_CAP = 12
_names = {}


def name(uid: str | None) -> str:
    if not uid:
        return ""
    if uid not in _names:
        u = db.one("SELECT name FROM users WHERE id=?", (uid,))
        _names[uid] = u["name"] if u else uid
    return _names[uid]


def relay(bug_id: str, step: str, actor: str, kind: str, note: str = ""):
    b = db.one("SELECT relay, project, release FROM bugs WHERE id=?", (bug_id,))
    items = json.loads(b["relay"] or "[]")
    items = [i for i in items if i["step"] != step]
    items.append({"step": step, "actor": actor, "kind": kind, "note": note, "at": time.time()})
    items.sort(key=lambda i: STAGES.index(i["step"]))
    db.x("UPDATE bugs SET relay=? WHERE id=?", (json.dumps(items), bug_id))
    db.event(b["project"], b["release"], actor, f"bug_{step}", {"bug": bug_id, "note": note})


import queue

_q: "queue.Queue[tuple[str, str]]" = queue.Queue()


def _worker():
    while True:
        project, bug_id = _q.get()
        try:
            auto_work(project, bug_id)
        except Exception as e:  # keep the relay alive for the next bug
            print(f"[pipeline] {bug_id}: {e}", flush=True)


threading.Thread(target=_worker, daemon=True).start()


def start(project: str, bug_id: str):
    """One bug at a time, so proposals see each other's load and cards visibly move in order."""
    _q.put((project, bug_id))


def auto_work(project: str, bug_id: str):
    """What the agents do the moment a bug arrives, each one a harness run with a visible trace."""
    from .harness.loop import run
    from .harness.tools import Ctx

    b = db.one("SELECT * FROM bugs WHERE id=?", (bug_id,))
    rel = db.one("SELECT * FROM releases WHERE id=?", (b["release"],)) if b["release"] else None
    relay(bug_id, "reported", name(b["reporter"]) if b["reporter_kind"] == "human" else "Agent tester", b["reporter_kind"],
          "Found on staging by the agent tester" if b["reporter_kind"] == "agent" else f"{b['severity']} on {b['env']}")
    ctx = Ctx(project, bug=b, release=rel)

    rc = run("root-cause", ctx, "bug", bug_id, "find the root cause")
    ctx.bug = db.one("SELECT * FROM bugs WHERE id=?", (bug_id,))
    rp = run("repro", ctx, "bug", bug_id, "reproduce it")
    last = rp["steps"][-1] if rp["steps"] else {"tool": "", "summary": ""}
    asked = last["tool"] == "ask_human"
    db.x("UPDATE bugs SET stage='reproduced' WHERE id=?", (bug_id,))
    relay(bug_id, "reproduced", "Repro agent", "agent",
          ("Couldn't run it safely, asked a tester to reproduce and record it" if asked else last["summary"]))
    time.sleep(0.3)
    theory = (rc["final"] or {}).get("theory") or {"confidence": 0, "text": "no theory"}
    db.x("UPDATE bugs SET stage='cause_found' WHERE id=?", (bug_id,))
    relay(bug_id, "cause_found", "Root cause agent", "agent", f"{theory['confidence']}%: {theory['text'][:140]}")
    time.sleep(0.3)
    ctx.bug = db.one("SELECT * FROM bugs WHERE id=?", (bug_id,))
    dp = run("dispatcher", ctx, "bug", bug_id, "propose an owner and an ETA")
    prop, eta = ctx.scratch.get("proposal") or {"user": None, "why": "dispatcher failed"}, ctx.scratch.get("eta", 0)
    relay(bug_id, "proposed", "Dispatcher", "agent",
          f"{name(prop['user'])}, about {eta:g}h. {prop['why']}" if prop["user"] else "No one available, needs a lead")
    route_bug(project, bug_id)
    expert_review(project, bug_id, prop.get("user"))


def route_bug(project: str, bug_id: str):
    """Give the bug to the track that owns the code it touches, so that team sees it."""
    from . import tracks
    from .harness.tools import Ctx, _bug_files
    b = db.one("SELECT * FROM bugs WHERE id=?", (bug_id,))
    t = tracks.route(project, _bug_files(Ctx(project, bug=b)))
    if t:
        db.x("UPDATE bugs SET track=? WHERE id=?", (t["id"], bug_id))
        db.event(project, b["release"], "dispatcher", "routed_to_track", {"bug": bug_id, "note": f"{t['name']} owns this code"})


def expert_review(project: str, bug_id: str, owner: str | None, force: bool = False):
    """When the person fixing this isn't senior in the area (or nobody is), bring in the area's expert."""
    from . import experts
    from .harness.loop import run
    from .harness.tools import Ctx, _bug_files
    b = db.one("SELECT * FROM bugs WHERE id=?", (bug_id,))
    ctx = Ctx(project, bug=b)
    files = _bug_files(ctx)
    e = experts.for_files(project, files)
    if not e:
        return None
    area = json.loads(e["paths"])
    if not force and owner and experts.is_senior(project, owner, area) and not experts.risk_words(files):
        return None
    return run("expert", ctx, "bug", bug_id, "review the fix plan like a senior would")


# ---------------- reproduce ----------------

def _fields_near(project: str, ev: dict) -> list[str]:
    fields = []
    for s in ev["steps"]:
        for c in s["cites"]:
            if c.startswith("map:") or c.startswith("field:"):
                fields.append(c.split(":", 1)[1])
            f = "file:lumen:" + c[5:] if c.startswith("code:") else ("file:" + c[4:].split("#")[0] if c.startswith("sym:") else None)
            if f:
                fields += [n["id"].split(":", 1)[1] for n in graph.neighbors(project, f, {"alters"}, "out")]
            if c.startswith("sym:"):
                fields += [n["id"].split(":", 1)[1] for n in graph.neighbors(project, c, {"uses_field", "writes_field"}, "out")]
    out = []
    for f in fields:
        if f not in out:
            out.append(f)
    return out[:2]


def _endpoints_near(project: str, ev: dict) -> list[str]:
    eps = []
    for s in ev["steps"]:
        for c in s["cites"]:
            if c.startswith("route:"):
                eps.append(c)
            if c.startswith("sym:"):
                eps += [n["id"] for n in graph.neighbors(project, c, {"handled_by"}, "in")]
                for caller in graph.neighbors(project, c, {"calls"}, "in")[:5]:
                    eps += [n["id"] for n in graph.neighbors(project, caller["id"], {"handled_by"}, "in")]
    return list(dict.fromkeys(eps))[:3]


def build_repro(project: str, bug: dict, ev: dict) -> dict:
    """Draft steps from the evidence. Runnable only when read-only: a generated query on the staging DB, or a GET on staging."""
    from . import staging
    from .engine import tests_covering

    proj = db.one("SELECT staging_url FROM projects WHERE id=?", (project,))
    fields = _fields_near(project, ev)
    eps = _endpoints_near(project, ev)
    steps, run, script = [], None, ""
    check = None
    for f in fields:
        for c in staging.field_checks(project, f):
            try:
                n = staging.run(project, c["sql"])["rows"][0][0]
            except Exception:
                n = 0
            if n:
                check = c
                break
        if check:
            break
    if check:
        sample = staging.sample_query(project, check)
        steps.append({"do": "Find affected records on staging (read-only)", "detail": sample})
        run = {"kind": "sql", "sql": sample}
        lang = {"mongodb": "MongoDB query (JSON)", "dynamodb": "DynamoDB PartiQL"}.get(staging.dialect(project), "SQL")
        script = f"-- staging database, read-only, {lang}\n{sample}"
    if eps:
        label = (graph.node(project, eps[0]) or {"label": eps[0]})["label"].split("@")[0]
        method, _, path = label.partition(" ")
        steps.append({"do": f"Call {method} {path} on staging" + (" as one of those records" if check else ""),
                      "detail": "The entry point that reaches the suspect code"})
        curl = f'curl -i -X {method} "$STAGING_URL{path}" -H "Authorization: Bearer $TEST_TOKEN"'
        script = (script + "\n\n" if script else "") + curl
        if not run and method == "GET" and not re.search(r"[{:<\[]", path) and proj["staging_url"]:
            run = {"kind": "http", "method": method, "path": path}
    if not steps:
        steps.append({"do": "Follow the reporter's steps", "detail": (bug["body"] or bug["title"])[:200]})
    if fields:
        steps.append({"do": "Check the data it depends on", "detail": ", ".join(fields)})
    steps.append({"do": "Expected vs actual", "detail": (bug["body"] or bug["title"])[:200]})
    tests = [t for s in ev.get("steps", []) for c in s["cites"] if c.startswith("sym:") for t in tests_covering(project, c)]
    if tests:
        steps.append({"do": "Turn it into a failing test", "detail": f"Copy the pattern in {tests[0].split(':', 2)[-1]}"})
    return {"steps": steps, "script": script, "run": run, "fields": fields, "endpoints": eps, "result": None}


def run_repro(project: str, bug_id: str, actor: str) -> dict:
    from . import staging
    b = db.one("SELECT * FROM bugs WHERE id=?", (bug_id,))
    rp = json.loads(b["repro"] or "{}")
    r = rp.get("run")
    if not r:
        raise ValueError("Nothing runnable yet. Follow the steps by hand.")
    if r["kind"] == "sql":
        out = staging.run(project, r["sql"])
        res = {"ok": bool(out["rows"]), "summary": f"Reproduced: {len(out['rows'])} affected records" if out["rows"] else "Not reproduced: no rows",
               "table": {"columns": out["columns"], "rows": out["rows"][:10]}, "ms": out["ms"]}
    else:
        proj = db.one("SELECT staging_url FROM projects WHERE id=?", (project,))
        t0 = time.perf_counter()
        resp = httpx.get(proj["staging_url"].rstrip("/") + r["path"], timeout=10)
        res = {"ok": resp.status_code >= 400, "summary": f"GET {r['path']} returned {resp.status_code}", "table": None,
               "ms": round((time.perf_counter() - t0) * 1000, 1)}
    rp["result"] = res
    db.x("UPDATE bugs SET repro=? WHERE id=?", (json.dumps(rp, default=str), bug_id))
    relay(bug_id, "reproduced", name(actor) or "Repro agent", "human" if actor else "agent", res["summary"])
    return res


# ---------------- ETA ----------------

def estimate_eta(project: str, bug: dict, ev: dict) -> tuple[float, str]:
    cites = [c for s in ev["steps"] for c in s["cites"]]
    pr = next((c for c in cites if c.startswith("pr:")), None)
    files = len(graph.neighbors(project, pr, {"touches"}, "out")) if pr else 1
    code = [c for c in cites if c.startswith(("sym:", "code:"))]
    callers = sum(len(graph.neighbors(project, c, {"calls"}, "in")) for c in code if c.startswith("sym:"))
    fields = _fields_near(project, ev)
    from .engine import tests_covering

    tested = any(tests_covering(project, c) for c in code if c.startswith("sym:"))
    hours = 1.0 + 0.5 * min(files, 4) + 0.25 * min(callers, 8)
    factors = [f"{files} file{'s' if files != 1 else ''} in the change"]
    if callers:
        factors.append(f"{callers} callers to re-check")
    if fields:
        hours += 1.0
        factors.append("data backfill for " + fields[0])
    if not tested:
        hours += 0.5
        factors.append("no test covers it yet")
    past = db.q("SELECT accepted_at, fixed_at FROM bugs WHERE project=? AND fixed_at IS NOT NULL AND accepted_at IS NOT NULL", (project,))
    if len(past) >= 2:
        avg = sum((p["fixed_at"] - p["accepted_at"]) / 3600 for p in past) / len(past)
        hours = (hours + avg) / 2
        factors.append(f"similar fixes here took {avg:.1f}h")
    return max(1.0, round(hours * 2) / 2), ", ".join(factors)


# ---------------- owner proposal ----------------

def load(project: str) -> dict[str, float]:
    """Hours of accepted or proposed fix work per person, from ETAs."""
    out: dict[str, float] = {}
    for b in db.q("SELECT assignee, proposed, eta_owner, eta_agent, stage FROM bugs WHERE project=? AND stage IN ('proposed','fixing')", (project,)):
        who = b["assignee"] if b["stage"] == "fixing" else b["proposed"]
        if who:
            out[who] = out.get(who, 0) + (b["eta_owner"] or b["eta_agent"] or 0)
    return out


def propose_owner(project: str, bug_id: str, ev: dict, eta: float, exclude: set[str] | None = None) -> dict:
    exclude = exclude or set()
    cites = [c for s in ev["steps"] for c in s["cites"]]
    prs = [c for c in cites if c.startswith("pr:")][:2]
    score, reasons = {}, {}

    outsiders: dict[str, str] = {}

    def credit(login: str, pts: float, why: str):
        u = db.one("SELECT id FROM users WHERE github=?", (login,))
        if not u or db.role_of(u["id"], project) not in ("engineer", "lead", "owner"):
            if not login.endswith("[bot]"):
                outsiders.setdefault(login, why)
            return
        if u["id"] in exclude:
            return
        score[u["id"]] = score.get(u["id"], 0) + pts
        reasons.setdefault(u["id"], []).append(why)

    for i, pr in enumerate(prs):
        label = (graph.node(project, pr) or {"label": pr})["label"].split(" ")[0]
        for n in graph.neighbors(project, pr, {"authored"}, "in"):
            credit(n["id"].split(":", 1)[1], 4 - i, f"wrote {label}")
        for n in graph.neighbors(project, pr, {"reviewed"}, "in"):
            credit(n["id"].split(":", 1)[1], 3 - i, f"reviewed {label}")
    for c in cites:
        f = "file:lumen:" + c[5:] if c.startswith("code:") else ("file:" + c[4:].split("#")[0] if c.startswith("sym:") else None)
        if f:
            for n in graph.neighbors(project, f, {"owns"}, "in"):
                credit(n["id"].split(":", 1)[1], 2, "owns the code")
    busy = load(project)
    ranked = []
    for uid, sc in sorted(score.items(), key=lambda x: -x[1]):
        u = db.one("SELECT name, shift, hours_today FROM users WHERE id=?", (uid,))
        left = HOURS_CAP - u["hours_today"] - busy.get(uid, 0)
        ranked.append({"user": uid, "name": u["name"], "why": ", ".join(dict.fromkeys(reasons[uid])), "capacity": round(left, 1),
                       "shift": u["shift"], "fits": u["shift"] == "on" and left >= eta})
    best = ranked[0] if ranked else None
    pick = next((r for r in ranked if r["fits"]), None)
    note = ""
    if best and pick and best["user"] != pick["user"]:
        reason = "is off shift" if best["shift"] != "on" else f"has {max(best['capacity'], 0):g}h left today"
        note = f" {best['name'].split()[0]} knows it best but {reason}."
    if not pick:
        free: list = []
        for m in db.q("""SELECT u.id, u.name, u.shift, u.hours_today FROM members m JOIN users u ON u.id=m.user
                         WHERE m.project=? AND m.role='engineer'""", (project,)):
            left = HOURS_CAP - m["hours_today"] - busy.get(m["id"], 0)
            if m["shift"] == "on" and left >= eta and m["id"] not in exclude:
                free.append((left, m))
        if free and best and best["shift"] is not None:
            left, m = max(free, key=lambda x: x[0])
            reason = "is off shift" if best["shift"] != "on" else f"has {max(best['capacity'], 0):g}h left today"
            return {"user": m["id"], "why": f"{best['name'].split()[0]} knows it best but {reason}. {m['name'].split()[0]} has {left:g}h free, "
                    f"pair with {best['name'].split()[0]} for a 20 minute handover.", "pair": best["user"], "alternates": ranked[:3]}
        invite = f" {next(iter(outsiders))} {outsiders[next(iter(outsiders))]} but isn't in this project: invite them?" if outsiders else ""
        if free:
            left, m = max(free, key=lambda x: x[0])
            return {"user": m["id"], "why": f"Nobody on the project has touched this code.{invite} {m['name']} has {left:g}h free.",
                    "invite": list(outsiders)[:3], "alternates": ranked[:3]}
        return {"user": None, "why": ("Nobody has capacity today." + note + invite).strip(), "invite": list(outsiders)[:3], "alternates": ranked[:3]}
    return {"user": pick["user"], "why": pick["why"] + "." + note, "alternates": [r for r in ranked if r["user"] != pick["user"]][:3]}


# ---------------- people actions ----------------

def accept(bug_id: str, user: str, eta: float | None):
    b = db.one("SELECT * FROM bugs WHERE id=?", (bug_id,))
    eta = eta or b["eta_agent"]
    db.x("UPDATE bugs SET assignee=?, eta_owner=?, stage='fixing', status='in_progress', accepted_at=? WHERE id=?", (user, eta, time.time(), bug_id))
    agreed = "agreed with the agent" if eta == b["eta_agent"] else f"agent estimated {b['eta_agent']:g}h"
    relay(bug_id, "fixing", name(user), "human", f"ETA {eta:g}h ({agreed})")


def decline(bug_id: str, user: str, reason: str):
    b = db.one("SELECT * FROM bugs WHERE id=?", (bug_id,))
    ev = json.loads(b["evidence"]) if (b["evidence"] or "").startswith("{") else {"steps": []}
    prop = propose_owner(b["project"], bug_id, ev, b["eta_agent"] or 2, exclude={b["proposed"]})
    db.x("UPDATE bugs SET proposed=?, proposed_why=? WHERE id=?", (prop["user"], json.dumps(prop), bug_id))
    relay(bug_id, "proposed", "Dispatcher", "agent",
          f"{name(user)} passed ({reason or 'no reason'}). Now proposing {name(prop['user']) or 'a lead'}. {prop['why']}")


def reassign(bug_id: str, lead: str, to: str):
    db.x("UPDATE bugs SET proposed=?, stage='proposed' WHERE id=?", (to, bug_id))
    relay(bug_id, "proposed", name(lead), "human", f"Moved to {name(to)}")


def mark_fixed(bug_id: str, user: str):
    db.x("UPDATE bugs SET stage='fixed', status='fixed', fixed_at=? WHERE id=?", (time.time(), bug_id))
    relay(bug_id, "fixed", name(user), "human", "Fix deployed to staging")


def verify(bug_id: str, user: str):
    db.x("UPDATE bugs SET stage='verified', status='verified', verified_by=? WHERE id=?", (user, bug_id))
    relay(bug_id, "verified", name(user), "human", "Verified on staging")
    from .experts import settle_bug
    settle_bug(bug_id)


# ---------------- views ----------------

def diagram(project: str, bug: dict, ev: dict, rp: dict) -> dict:
    """Focused path from symptom to the people who can fix it. Columns, not a hairball."""
    cols = ["What broke", "Entry point", "Code", "Data", "Change", "Who knows it"]
    # Which relay step confirms each column, so the UI can light nodes up as agents confirm them.
    col_step = ["reported", "reproduced", "cause_found", "reproduced", "cause_found", "proposed"]
    nodes, edges = [], []

    def add(nid, label, col, hot=False, kind=""):
        if not any(n["id"] == nid for n in nodes):
            nodes.append({"id": nid, "label": label[:42], "col": col, "hot": hot, "kind": kind, "step": col_step[col]})
    add("bug", bug["title"], 0, True)
    cites = [c for s in ev.get("steps", []) for c in s["cites"]]
    test = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_|_test\.|\.spec\.|\.test\.|#test_", re.I)
    code = [c for c in cites if c.startswith(("sym:", "code:")) and not test.search(c)][:3]
    for i, e in enumerate(rp.get("endpoints", [])[:2]):
        add(e, (graph.node(project, e) or {"label": e})["label"], 1, i == 0)
        edges.append(("bug", e))
    for i, c in enumerate(code):
        label = c.split(":", 2)[-1].split("#")[-1] if c.startswith("sym:") else c.split("/")[-1]
        add(c, label, 2, i == 0)
        for n in nodes:
            if n["col"] == 1:
                edges.append((n["id"], c))
        if not any(n["col"] == 1 for n in nodes):
            edges.append(("bug", c))
    for f in rp.get("fields", [])[:3]:
        add("field:" + f, f, 3, f == (rp.get("fields") or [None])[0])
        if code:
            edges.append((code[0], "field:" + f))
    prs = [c for c in cites if c.startswith("pr:")][:2]
    for i, pr in enumerate(prs):
        add(pr, (graph.node(project, pr) or {"label": pr})["label"], 4, i == 0)
        src = code[0] if code else "bug"
        edges.append((src, pr))
        for n in graph.neighbors(project, pr, {"authored", "reviewed"}, "in")[:2]:
            login = n["id"].split(":", 1)[1]
            u = db.one("SELECT id, name FROM users WHERE github=? OR id=?", (login, login))
            pid = "person:" + (u["id"] if u else n["id"].split(":", 1)[1])
            add(pid, (u["name"] if u else login) + (" (wrote)" if n["edge"] == "authored" else " (reviewed)"), 5,
                bool(u and u["id"] in (bug.get("proposed"), bug.get("assignee"))), "person")
            edges.append((pr, pid))
    if not code:
        for f in rp.get("fields", [])[:1]:
            edges.append(("bug", "field:" + f))
    return {"columns": cols, "col_step": col_step, "nodes": nodes, "edges": [{"from": a, "to": b} for a, b in dict.fromkeys(edges)]}


def board(release: dict) -> dict:
    project = release["project"]
    people = db.q("""SELECT u.id, u.name, u.shift, u.hours_today, m.role FROM members m JOIN users u ON u.id=m.user
                     WHERE m.project=? AND m.role IN ('engineer','lead','owner')""", (project,))
    busy = load(project)
    lanes = []
    for p in people:
        fixes = db.q("""SELECT id, title, stage, eta_owner, eta_agent FROM bugs WHERE release=? AND
                        ((stage='fixing' AND assignee=?) OR (stage='proposed' AND proposed=?))""", (release["id"], p["id"], p["id"]))
        if not fixes and p["shift"] != "on":
            continue
        left = HOURS_CAP - p["hours_today"]
        lanes.append({"id": p["id"], "name": p["name"], "shift": p["shift"], "worked": p["hours_today"], "cap": HOURS_CAP,
                      "left": left, "queued": round(busy.get(p["id"], 0), 1), "over": busy.get(p["id"], 0) > left,
                      "fixes": [{"id": f["id"], "title": f["title"], "stage": f["stage"], "hours": f["eta_owner"] or f["eta_agent"] or 0,
                                 "agreed": f["stage"] == "fixing"} for f in fixes]})
    lanes.sort(key=lambda l: (-len(l["fixes"]), l["name"]))
    return {"lanes": lanes, "stages": STAGES}
