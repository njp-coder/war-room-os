"""Release lifecycle: Plan -> Build -> Staging -> Go/no-go -> Live -> Closed.

Everything here is code over the graph and memory; the model is used only for
root-cause theories (with a citation gate) and falls back to evidence-only text.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

import httpx

from . import db, gateway, graph
from .memory import memory_for, f_eq

STAGES = ["plan", "build", "staging", "go_no_go", "live", "closed"]
MONEY = re.compile(r"(billing|payment|charge|invoice|refund|checkout|subscription|auth|login|password|session|token)", re.I)
SEV_RANK = {"blocker": 0, "critical": 0, "major": 1, "minor": 2, "trivial": 3}


# ---------------- scope ----------------

def linked_prs(release: dict) -> list[dict]:
    rows = db.q("SELECT id, label, props FROM nodes WHERE project=? AND type='pr'", (release["project"],))
    out = []
    st, sv = release["scope_type"], (release["scope_value"] or "").strip()
    for r in rows:
        p = json.loads(r["props"] or "{}")
        num = r["id"].split("#")[-1]
        ok = (st == "milestone" and p.get("milestone") == sv) or (st == "label" and sv in (p.get("labels") or [])) or \
             (st == "branch" and sv in (p.get("base"), p.get("head"))) or \
             (st == "prs" and num in [x.strip().lstrip("#") for x in sv.split(",")]) or \
             (st == "open" and p.get("state") == "open")
        if ok:
            out.append({"id": r["id"], "label": r["label"], **p})
    return out


def release_files(release: dict) -> list[str]:
    files = set()
    for pr in linked_prs(release):
        files |= {n["id"] for n in graph.neighbors(release["project"], pr["id"], {"touches"}, "out")}
    return sorted(files)


# ---------------- pre-mortem on the release diff ----------------

def changed_symbols(release: dict) -> dict[str, set[str]]:
    """file node -> symbols whose line range overlaps the release's diff hunks."""
    project = release["project"]
    out: dict[str, set[str]] = {}
    for pr in linked_prs(release):
        repo = pr["id"][3:].split("#")[0]
        for row in db.q("SELECT path, hunks FROM pr_files WHERE project=? AND pr=?", (project, pr["id"])):
            fid = f"file:{repo}:{row['path']}"
            hunks = json.loads(row["hunks"] or "[]")
            syms = out.setdefault(fid, set())
            for n in graph.neighbors(project, fid, {"defines"}, "out"):
                node = graph.node(project, n["id"]) or {"props": {}}
                lo, hi = node["props"].get("line", 0), node["props"].get("end", node["props"].get("line", 0))
                if not hunks or any(a <= hi and b >= lo for a, b in hunks):
                    syms.add(n["id"])
    return out


def readable(project: str, target: str) -> str:
    kind, _, rest = target.partition(":")
    if kind == "route":
        return (graph.node(project, target) or {"label": rest})["label"].split("@")[0]
    if kind == "sym":
        path, _, name = rest.partition("#")
        return f"{name} in {path.split('/')[-1]}"
    if kind == "file":
        return rest.split(":", 1)[-1].split("/")[-1]
    return rest


def premortem(release: dict) -> list[dict]:
    """Few, ranked risks from what this release actually changed."""
    project, rid = release["project"], release["id"]
    if db.q("SELECT 1 FROM projects WHERE id=? AND demo=1", (project,)):
        return db.q("SELECT * FROM risks WHERE release=?", (rid,))
    from . import staging
    from .engine import tests_covering
    changed = changed_symbols(release)
    all_changed = {s for syms in changed.values() for s in syms}
    live = staging.connected(project)
    risks: dict[str, tuple] = {}

    def add(target, kind, score, note):
        if target not in risks or risks[target][1] < score:
            risks[target] = (kind, score, note)

    # 1. data: tables whose model class or migration changed -> required fields become data checks
    for t in db.q("SELECT id, label, props FROM nodes WHERE project=? AND type='table'", (project,)):
        props = json.loads(t["props"] or "{}")
        model_changed = any(s.endswith("#" + str(props.get("model"))) for s in all_changed) if props.get("model") else False
        fields = graph.neighbors(project, t["id"], {"has_field"}, "out")
        altered = [f["id"] for f in fields if any(e["id"] in changed for e in graph.neighbors(project, f["id"], {"alters"}, "in"))]
        if not (model_changed or altered):
            continue
        required = [f["id"] for f in fields if (graph.node(project, f["id"]) or {"props": {}})["props"].get("nullable") is False]
        for fid in (altered + [r for r in required if r not in altered])[:4]:
            add(fid, "data may break on this change", 8 if live else 6,
                f"{t['label']} changed in this release. Existing rows must still satisfy {fid.split(':', 1)[1]}.")
    # 2. endpoints served by changed code
    for s in all_changed:
        for ep in graph.neighbors(project, s, {"handled_by"}, "in"):
            label = readable(project, ep["id"])
            money = bool(MONEY.search(label))
            untested = not tests_covering(project, s)
            add(ep["id"], "endpoint behaviour changed", 6 + (2 if money else 0) + (1 if untested else 0),
                f"{label} is served by {readable(project, s)}, changed in this release" + (". No test covers it." if untested else "."))
    # 3. widely used code changed
    for s in all_changed:
        callers = [c for c in graph.neighbors(project, s, {"calls"}, "in")]
        if len(callers) >= 5:
            add(s, "widely used code changed", 5 + min(len(callers), 10) / 2,
                f"{readable(project, s)} has {len(callers)} callers." + ("" if tests_covering(project, s) else " No test covers it."))
    ranked = sorted(risks.items(), key=lambda kv: -kv[1][1])[:12]
    db.x("DELETE FROM risks WHERE release=? AND status='untested'", (rid,))
    rows = [(f"{rid}:{t}", project, rid, t, kind, "high" if score >= 7 else "medium", note) for t, (kind, score, note) in ranked]
    db.xmany("INSERT OR IGNORE INTO risks(id, project, release, target, kind, level, note) VALUES(?,?,?,?,?,?,?)", rows)
    db.event(project, rid, "risk-agent", "premortem", {"risks": len(rows), "changed_symbols": len(all_changed)})
    return db.q("SELECT * FROM risks WHERE release=? ORDER BY level='high' DESC", (rid,))


# ---------------- bugs ----------------

def suggest_priority(project: str, title: str, body: str, severity: str) -> tuple[str, str]:
    text = f"{title} {body}"
    hits = memory_for(project).search("knowledge", text, top_k=4)
    seeds = [h.id for h in hits if h.metadata.get("type") in ("symbol", "endpoint", "field")]
    radius = graph.blast_radius(project, seeds, max_depth=2)["size"] if seeds else 0
    score = {0: 3, 1: 2, 2: 1, 3: 0}.get(SEV_RANK.get(severity, 2), 1)
    reasons = [f"severity {severity}"]
    if MONEY.search(text):
        score += 1
        reasons.append("money or auth path")
    if radius >= 20:
        score += 1
        reasons.append(f"blast radius {radius} nodes")
    settings = json.loads((db.one("SELECT settings FROM projects WHERE id=?", (project,)) or {}).get("settings") or "{}")
    if settings.get("client_facing"):
        score += 1
        reasons.append("client-facing product")
    pr = "P0" if score >= 5 else "P1" if score == 4 else "P2" if score == 3 else "P3"
    return pr, ", ".join(reasons)


def create_bug(project: str, release: str | None, env: str, title: str, body: str, severity: str,
               reporter: str, reporter_kind: str = "human", priority: str | None = None, blocker: bool = False,
               evidence: str = "", links: list[str] | None = None) -> dict:
    suggested, why = suggest_priority(project, title, body, severity)
    dupes = [h for h in memory_for(project).search("signals", f"{title} {body}", top_k=3, flt=f_eq("type", "bug"))
             if h.score >= (0.6 if memory_for(project).backend == "local" else 0.45) and h.metadata.get("release") == (release or "")]
    bid = db.uid("bug")
    db.x("""INSERT INTO bugs(id, project, release, env, title, body, severity, priority, suggested_priority, blocker, status,
            reporter, reporter_kind, created, links, evidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (bid, project, release, env, title, body, severity, priority or suggested, f"{suggested}|{why}", int(blocker),
          "open", reporter, reporter_kind, time.time(), json.dumps((links or []) + [f"dupe:{d.id}" for d in dupes]), evidence))
    memory_for(project).add("signals", [{"id": bid, "text": f"{title}. {body}"[:800],
                                         "metadata": {"type": "bug", "env": env, "release": release or "", "status": "open"}}])
    db.event(project, release, reporter, "bug_reported", {"bug": bid, "title": title, "env": env, "kind": reporter_kind})
    from . import pipeline
    pipeline.start(project, bid)  # agents reproduce, find the cause, propose an owner and an ETA
    return db.one("SELECT * FROM bugs WHERE id=?", (bid,))


def investigate_bug(project: str, bug_id: str) -> dict:
    """Evidence packet from memory + graph, then a theory (model with citation gate, or evidence-only)."""
    bug = db.one("SELECT * FROM bugs WHERE id=?", (bug_id,))
    mem = memory_for(project)
    text = f"{bug['title']} {bug['body']}"
    steps, cites = [], {}
    code = [h for h in mem.search("knowledge", text, top_k=8) if h.metadata.get("type") in ("symbol", "endpoint", "field", "code", "mapping")][:4]
    steps.append({"name": "Where in the code", "summary": "; ".join(h.text[:110] for h in code) or "No matching code.",
                  "cites": [h.id for h in code]})
    rel = db.one("SELECT * FROM releases WHERE id=?", (bug["release"],)) if bug["release"] else None
    release_pr_ids = {p["id"] for p in linked_prs(rel)} if rel else set()
    changes = []
    for h in code:
        file_id = h.id if h.id.startswith("file:") else None
        if h.id.startswith("code:"):  # demo project cards
            file_id = "file:lumen:" + h.id[5:]
        elif h.id.startswith("map:") or h.id.startswith("field:"):
            fid = "field:" + h.id.split(":", 1)[1]
            for e in db.q("SELECT src, source FROM edges WHERE project=? AND dst=? AND type='alters' AND valid_to IS NULL", (project, fid)):
                if e["source"].startswith("pr:"):  # the PR that changed this exact field
                    changes += [(e["source"] in release_pr_ids, e["source"])] * 2
                file_id = e["src"]
        elif h.id.startswith("sym:"):
            file_id = "file:" + h.id[4:].split("#")[0]
        elif h.id.startswith("route:"):
            handlers = graph.neighbors(project, h.id, {"handled_by"}, "out")
            file_id = "file:" + handlers[0]["id"][4:].split("#")[0] if handlers else None
        if file_id:
            for pr in graph.neighbors(project, file_id, {"touches"}, "in"):
                changes.append((pr["id"] in release_pr_ids, pr["id"]))
    for h in mem.search("changes", text, top_k=4, flt=f_eq("type", "pr")):
        changes.append((h.id in release_pr_ids, h.id))
    votes: dict[str, float] = {}
    for i, (in_rel, pid) in enumerate(changes):
        votes[pid] = votes.get(pid, 0) + 1 + (0.5 if in_rel else 0) + 1 / (10 + i)  # more evidence paths, in-release, earlier hits
    ranked = sorted(votes, key=lambda p: -votes[p])
    top_changes = ranked[:3]
    steps.append({"name": "What changed", "summary": "; ".join((graph.node(project, p) or {"label": p})["label"] +
                  (" (in this release)" if p in release_pr_ids else "") for p in top_changes) or "No linked change found.",
                  "cites": top_changes})
    people = []
    for p in top_changes:
        people += [n["id"].split(":", 1)[1] for n in graph.neighbors(project, p, {"authored", "reviewed"}, "in")]
    prior = [b for b in db.q("SELECT id, title, env, status FROM bugs WHERE project=? AND id<>? AND status IN ('fixed','verified','closed')",
                             (project, bug_id)) if b["env"] == "staging" and bug["env"] == "production"]
    regress = [b for b in prior if any(w in b["title"].lower() for w in bug["title"].lower().split() if len(w) > 4)]
    steps.append({"name": "Seen before", "summary": ("Possible regression of " + ", ".join(b["title"] for b in regress[:2])) if regress else "No earlier fix matches.",
                  "cites": [b["id"] for b in regress[:2]]})
    for h in code:
        cites[h.id] = h.text
    theory, source = None, "evidence"
    try:
        reply = gateway.llm_call("reason",
                                 "Root-cause agent. Use only the evidence. Cite card IDs. JSON: {\"text\":..., \"confidence\":0-100, \"cites\":[ids]}",
                                 "\n".join(f"[{s['name']}] {s['summary']} cites={s['cites']}" for s in steps) + f"\nBug: {text[:600]}", 300)
        valid = [c for c in reply.get("cites", []) if any(c in s["cites"] for s in steps)]
        if reply.get("text") and valid:
            theory, source = {"text": reply["text"], "confidence": int(reply.get("confidence", 50)), "cites": valid}, "model"
    except gateway.Offline:
        pass
    except Exception as e:
        db.event(project, bug["release"], "root-cause", "model_error", {"error": str(e)[:200]})
    if not theory:
        if code and top_changes:
            where = code[0].text.split(":")[0]
            ch = (graph.node(project, top_changes[0]) or {"label": top_changes[0]})["label"]
            conf = 55 + (15 if top_changes[0] in release_pr_ids else 0) + (10 if regress else 0)
            theory = {"text": f"Most likely in {where}, last changed by PR {ch}" + (", which ships in this release." if top_changes[0] in release_pr_ids else "."),
                      "confidence": conf, "cites": [code[0].id, top_changes[0]]}
        else:
            theory = {"text": "Evidence is weak. Needs someone who knows this area.", "confidence": 20, "cites": []}
    suggestion = _suggest_assignee(project, people)
    result = {"steps": steps, "theory": theory, "source": source, "assignee": suggestion, "regression": bool(regress)}
    db.x("UPDATE bugs SET evidence=? WHERE id=?", (json.dumps(result), bug_id))
    db.event(project, bug["release"], "root-cause", "bug_investigated", {"bug": bug_id, "source": source})
    return result


def _suggest_assignee(project: str, logins: list[str]) -> dict | None:
    for login in logins:
        u = db.one("SELECT id, name, shift, hours_today FROM users WHERE github=?", (login,))
        if u and u["shift"] == "on" and u["hours_today"] < 12 and db.role_of(u["id"], project):
            return {"user": u["id"], "name": u["name"], "why": f"{login} wrote or reviewed the change"}
    if logins:
        return {"user": None, "name": logins[0], "why": f"{logins[0]} wrote or reviewed the change (not a member here yet)"}
    return None


# ---------------- agent tester ----------------

DEMO_CHECKS = {
    "users.email": ("SELECT count(*) FROM users WHERE email IS NULL AND batch='batch-3'",
                    "Legacy accounts with NULL email will fail v2 email validation at sign-in"),
    "subscriptions.billing_anchor": ("SELECT count(*) FROM subscriptions WHERE anchor_v2 <> anchor_legacy AND tz <> 'UTC'",
                                     "Billing anchors shift a day for non-UTC accounts, second charge in the cycle"),
    "users.display_name": ("SELECT count(*) FROM users WHERE name_encoding='latin1' AND signup_year < 2019",
                           "Pre-2019 latin1 display names will be double-encoded"),
    "preferences.theme": ("SELECT count(*) FROM users WHERE theme NOT IN ('LIGHT','DARK')",
                          "LEGACY_DARK theme has no v2 mapping and falls back to default"),
    "users.deleted_at": ("SELECT count(*) FROM users WHERE deleted_at IS NOT NULL",
                         "Soft-deleted users will be migrated as active and receive email"),
}


def tester_run(release: dict) -> dict:
    project, rid = release["project"], release["id"]
    proj = db.one("SELECT * FROM projects WHERE id=?", (project,))
    ran = passed = failed = skipped = 0
    for t in db.q("SELECT * FROM tests WHERE release=? AND by='agent-tester' AND status IN ('planned','failed')", (rid,)):
        status, result = "skipped", ""
        if t["kind"] == "http" and proj["staging_url"]:
            method, path = t["target"].split(":", 1)[1].split("@")[0].split(" ", 1)
            if method != "GET" or re.search(r"[{:<\[]", path):
                status, result = "manual", "Needs parameters or a write method. Left for a human tester."
            else:
                try:
                    r = httpx.get(proj["staging_url"].rstrip("/") + path, timeout=10)
                    status = "failed" if r.status_code >= 500 else "passed"
                    result = f"GET {path} -> {r.status_code}"
                except Exception as e:
                    status, result = "failed", f"GET {path} -> {type(e).__name__}"
        elif t["kind"] == "http":
            status, result = "manual", "No staging URL set for this project."
        elif t["kind"] == "data" and proj["demo"]:
            field = t["target"].split(":", 1)[1]
            if field in DEMO_CHECKS:
                sql, finding = DEMO_CHECKS[field]
                con = sqlite3.connect(f"file:{db.PATH.parent / 'legacy.db'}?mode=ro", uri=True)
                n = con.execute(sql).fetchone()[0]
                con.close()
                status = "failed" if n else "passed"
                result = f"{n:,} rows at risk. {sql}"
                if n:
                    create_bug(project, rid, "staging", finding, f"Agent tester ran on the staging replica: {result}", "critical",
                               "agent-tester", "agent", blocker=True, evidence=result, links=[t["target"]])
            else:
                status, result = "manual", "No automated check for this field yet. Left for a human tester."
        elif t["kind"] == "data":
            status, result = "manual", "No staging database connected. Left for a human tester."
        else:
            status, result = "manual", "Needs a human tester."
        ran += status in ("passed", "failed")
        passed += status == "passed"
        failed += status == "failed"
        skipped += status == "manual"
        db.x("UPDATE tests SET status=?, result=?, run_at=? WHERE id=?", (status, result, time.time(), t["id"]))
        if status in ("passed", "failed"):
            db.x("UPDATE risks SET status=?, tested_by='agent-tester', tested_at=? WHERE id=?",
                 ("verified" if status == "passed" else "failed", time.time(), t["risk"]))
    db.event(project, rid, "agent-tester", "run", {"ran": ran, "passed": passed, "failed": failed, "manual": skipped})
    return {"ran": ran, "passed": passed, "failed": failed, "manual": skipped}


# ---------------- gates ----------------

def gates(release: dict) -> list[dict]:
    rid, project = release["id"], release["project"]
    blockers = db.q("SELECT id FROM bugs WHERE release=? AND blocker=1 AND status NOT IN ('verified','closed')", (rid,))
    high = db.q("SELECT id, status FROM risks WHERE release=? AND level='high'", (rid,))
    untested = [r for r in high if r["status"] != "verified"]
    engineers = db.q("""SELECT u.name FROM members m JOIN users u ON u.id=m.user WHERE m.project=? AND m.role IN ('lead','engineer','owner')
                        AND u.shift='on' AND u.hours_today < 12""", (project,))
    settings = json.loads((db.one("SELECT settings FROM projects WHERE id=?", (project,)) or {}).get("settings") or "{}")
    client_ok = db.one("SELECT 1 FROM events WHERE release=? AND kind='client_update_approved'", (rid,))
    out = [
        {"id": "blockers", "label": "No open release blockers", "ok": not blockers, "detail": f"{len(blockers)} open" if blockers else "All closed"},
        {"id": "risks", "label": "Every high risk verified on staging", "ok": not untested,
         "detail": f"{len(untested)} of {len(high)} not verified" if untested else f"{len(high)} verified"},
        {"id": "rollback", "label": "Rollback plan attached", "ok": bool((release["rollback_plan"] or "").strip()),
         "detail": "Attached" if (release["rollback_plan"] or "").strip() else "Missing"},
        {"id": "oncall", "label": "On-call cover for the window, nobody over 12h", "ok": len(engineers) >= 2,
         "detail": f"{len(engineers)} engineers available"},
    ]
    if settings.get("client_facing"):
        out.append({"id": "client", "label": "Client update approved", "ok": bool(client_ok), "detail": "Approved" if client_ok else "Not yet"})
    return out


def advance(release: dict, user: str, override_reason: str = "") -> dict:
    i = STAGES.index(release["stage"])
    if i + 1 >= len(STAGES):
        return release
    nxt = STAGES[i + 1]
    if release["stage"] == "go_no_go":
        failing = [g for g in gates(release) if not g["ok"]]
        if failing and not override_reason:
            raise ValueError("Gates failing: " + ", ".join(g["label"] for g in failing))
        if failing:
            db.event(release["project"], release["id"], user, "gate_override", {"reason": override_reason, "failing": [g["id"] for g in failing]})
    if nxt == "build" and not db.q("SELECT 1 FROM risks WHERE release=?", (release["id"],)):
        premortem(release)
    if nxt == "closed":
        from . import watch
        watch.finish(release, "release closed")
        db.x("UPDATE releases SET postmortem=?, closed=? WHERE id=?", (postmortem(release), time.time(), release["id"]))
    db.x("UPDATE releases SET stage=? WHERE id=?", (nxt, release["id"]))
    if nxt == "live":
        from . import watch
        watch.start(release)  # watch what this release changed, for as long as the team set
    db.event(release["project"], release["id"], user, "stage", {"from": release["stage"], "to": nxt})
    return db.one("SELECT * FROM releases WHERE id=?", (release["id"],))


# ---------------- postmortem ----------------

def postmortem(release: dict) -> str:
    rid = release["id"]
    bugs = db.q("SELECT * FROM bugs WHERE release=? ORDER BY created", (rid,))
    decisions = db.q("SELECT * FROM decisions WHERE release=? ORDER BY created", (rid,))
    overrides = db.q("SELECT data FROM events WHERE release=? AND kind='gate_override'", (rid,))
    lines = [f"# Postmortem: {release['name']}", "",
             f"Bugs: {len(bugs)} total, {sum(b['env'] == 'staging' for b in bugs)} caught on staging, "
             f"{sum(b['env'] == 'production' for b in bugs)} in production, {sum(b['reporter_kind'] == 'agent' for b in bugs)} found by the agent tester.", ""]
    lines += ["## Causes"] + [f"- {b['title']} ({b['env']}, {b['status']})" for b in bugs] + [""]
    if decisions:
        lines += ["## Decisions"] + [f"- {d['text']} ({d['by']})" for d in decisions] + [""]
    if overrides:
        lines += ["## Gate overrides"] + [f"- {json.loads(o['data'])['reason']}" for o in overrides] + [""]
    lines += ["## Learnings", "Confirmed causes are saved as playbooks and risk rules for the next release."]
    for b in bugs:
        if b["status"] in ("verified", "closed"):
            memory_for(release["project"]).add("knowledge", [{"id": f"playbook:{b['id']}", "text": f"Learned in {release['name']}: {b['title']}",
                                                            "metadata": {"type": "playbook"}}])
    return "\n".join(lines)
