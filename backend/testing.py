"""Shared test session: human testers and the agent tester work one list of checks.

How the agent knows how to test something, in order of trust:
  1. a recipe a human tester saved ("taught by Ishita")
  2. an existing test in the repo that covers the code ("pattern in tests/test_login.py")
  3. the PR that changed it ("drafted from PR #41")
  4. the code itself: route, fields, risk (a draft, marked as such)
What it can't do itself (UI flows, write actions, anything needing judgement) goes to a human tester,
with the agent's draft steps, the reason it needs help, and a thread to talk in.
Every check and bug carries evidence: agent request/response or SQL results, human recordings and screenshots.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import httpx

from . import netguard, db, graph
from .paths import DATA

UPLOADS = DATA / "uploads"


# ---------------- threads and evidence ----------------

def note(project: str, owner_type: str, owner_id: str, author: str, kind: str, text: str, ask: str | None = None):
    db.x("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?)", (db.uid("note"), project, owner_type, owner_id, author, kind, text, ask, time.time()))


def notes(owner_type: str, owner_id: str) -> list[dict]:
    return db.q("SELECT * FROM notes WHERE owner_type=? AND owner_id=? ORDER BY at", (owner_type, owner_id))


def attach_text(project: str, owner_type: str, owner_id: str, name: str, text: str, by: str, by_kind: str, kind: str = "text"):
    db.x("INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?,?,?,?)",
         (db.uid("att"), project, owner_type, owner_id, kind, name, None, text, by, by_kind, time.time()))


def attach_file(project: str, owner_type: str, owner_id: str, filename: str, data: bytes, content_type: str, by: str) -> dict:
    kind = "video" if content_type.startswith("video") else "image" if content_type.startswith("image") else "file"
    aid = db.uid("att")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[-80:] or "upload"
    folder = UPLOADS / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{aid}_{safe}"
    path.write_bytes(data)
    db.x("INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?,?,?,?)", (aid, project, owner_type, owner_id, kind, filename, str(path), None, by, "human", time.time()))
    return db.one("SELECT id, kind, name FROM attachments WHERE id=?", (aid,))


def attachments(owner_type: str, owner_id: str) -> list[dict]:
    return db.q("SELECT id, kind, name, text, by, by_kind, at FROM attachments WHERE owner_type=? AND owner_id=? ORDER BY at", (owner_type, owner_id))


# ---------------- how to test ----------------

def how_to_test(project: str, target: str) -> tuple[list[str], str, str | None]:
    """Steps, where that knowledge came from, and an optional read-only SQL check."""
    recipe = db.one("SELECT * FROM recipes WHERE project=? AND target=? ORDER BY created DESC LIMIT 1", (project, target))
    if recipe:
        return json.loads(recipe["steps"]), f"taught by {recipe['author']}", None
    from . import staging
    from .engine import tests_covering

    if target.startswith("field:"):
        field = target.split(":", 1)[1]
        checks = staging.field_checks(project, field)
        if checks:
            return ([f"Run on staging (read-only): {c['sql']}" for c in checks] +
                    ["Expect 0 each time. Anything else means rows will break after this change",
                     "Open one affected record in the app and confirm what the user sees"],
                    "generated from the schema: " + "; ".join(c["title"] for c in checks), json.dumps([c["sql"] for c in checks]))
        pr = db.one("SELECT source FROM edges WHERE project=? AND dst=? AND type='alters' AND source LIKE 'pr:%'", (project, target))
        src = f"drafted from PR {(graph.node(project, pr['source']) or {'label': pr['source']})['label'].split(' ')[0]}" if pr else "drafted from the schema"
        return [f"Find records where {field} is empty or invalid", "Check each one still loads and saves", "Record what the user sees"], src, None
    if target.startswith(("sym:", "route:", "file:")):
        node = target
        if target.startswith("route:"):
            h = graph.neighbors(project, target, {"handled_by"}, "out")
            node = h[0]["id"] if h else target
        tests = tests_covering(project, node)
        if tests:
            path = tests[0].split(":", 2)[-1]
            return [f"Run the existing test {path}", "Repeat its main case against staging", "Try one edge case it doesn't cover"], f"pattern in {path}", None
    if target.startswith("route:"):
        label = (graph.node(project, target) or {"label": target.split(":", 1)[1]})["label"].split("@")[0]
        return [f"Call {label} on staging with a test account", "Check the status code and the response shape", "Repeat with an older account"], "drafted from the route", None
    if target.startswith("ui:"):
        page = target.split(":", 1)[1]
        return [f"Open {page} on staging with a test account", "Go through the main action on that screen", "Record the screen, note anything that looks off"], "drafted from the UI files this release changes", None
    return ["Exercise the changed code path on staging", "Compare with production behaviour"], "drafted from the code", None


def _automatable(project: str, target: str, sql: str | None) -> tuple[bool, str]:
    from . import staging
    proj = db.one("SELECT staging_url FROM projects WHERE id=?", (project,))
    if sql:
        return (True, "") if staging.connected(project) else (False, "No staging database is connected, so I can't run the data check.")
    if target.startswith("route:"):
        label = (graph.node(project, target) or {"label": target.split(":", 1)[1]})["label"].split("@")[0]
        method, _, path = label.partition(" ")
        if not proj["staging_url"]:
            return False, "No staging URL is set, so I can't call the endpoint."
        if method != "GET" or re.search(r"[{:<\[]", path):
            return False, f"{method} {path} needs real data or writes something. I don't do writes on my own."
        return True, ""
    if target.startswith("ui:"):
        return False, "This needs a person looking at the screen."
    if target.startswith("field:"):
        return False, ("No staging database is connected, so I can't check rows myself." if not staging.connected(project)
                       else "I couldn't find this table or column in the staging database.")
    return False, "I have no safe way to run this automatically."


def _least_busy_tester(project: str) -> str | None:
    testers = db.q("SELECT u.id FROM members m JOIN users u ON u.id=m.user WHERE m.project=? AND m.role='tester' AND u.shift='on'", (project,))
    if not testers:
        return None
    load = {t["id"]: db.one("SELECT count(*) n FROM tests WHERE claimed_by=? AND status IN ('todo','running')", (t["id"],))["n"] for t in testers}
    return min(load, key=load.get)


UI_FILE = re.compile(r"(^|/)(pages|app|routes|views|screens|components)/(.+?)\.(tsx|jsx|vue|svelte|html)$")


def _ui_targets(release: dict) -> list[str]:
    """UI screens the release touches: a person should look at each."""
    from .releases import release_files
    pages = []
    for f in release_files(release):
        path = f.split(":", 2)[-1]
        m = UI_FILE.search(path)
        if m and not re.search(r"(test|spec|stories|/build/|email-templates|templates/)", path):
            page = "/" + re.sub(r"/(page|index|route|layout)$", "", m.group(3)).replace("[", ":").replace("]", "")
            pages.append(f"ui:{page}")
    return list(dict.fromkeys(pages))[:4]


def plan(release: dict) -> list[dict]:
    project, rid = release["project"], release["id"]
    from .pipeline import name
    targets = [(r["target"], f"{r['kind']} ({r['level']} risk)", r["id"]) for r in db.q("SELECT * FROM risks WHERE release=?", (rid,))]
    targets += [(t, "UI change in this release", None) for t in _ui_targets(release)]
    db.x("DELETE FROM tests WHERE release=? AND status='todo' AND bug IS NULL", (rid,))
    existing = {t["target"] for t in db.q("SELECT target FROM tests WHERE release=?", (rid,))}
    for target, what, risk in targets:
        if target in existing:
            continue
        steps, source, sql = how_to_test(project, target)
        auto, why = _automatable(project, target, sql)
        from .releases import readable
        charter = f"{readable(project, target)}: {what.split(' (')[0]}"
        tid = db.uid("test")
        claimed = None if auto else _least_busy_tester(project)
        db.x("""INSERT INTO tests(id, project, release, risk, charter, kind, target, status, by, result, owner_kind, claimed_by, how, source, help, created)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (tid, project, rid, risk, charter, json.dumps({"sql": sql}) if sql else ("auto" if auto else "manual"), target, "todo",
              "agent-tester" if auto else None, "", "agent" if auto else "human", claimed, json.dumps(steps), source, "" if auto else why, time.time()))
        if not auto:
            note(project, "test", tid, "Agent tester", "agent",
                 f"I can't run this one myself. {why} I drafted the steps ({source}). "
                 f"{('@' + name(claimed).split()[0] + ', can you take it and record what you see?') if claimed else 'Can a tester take it?'}",
                 ask=claimed)
    db.event(project, rid, "agent-tester", "plan", {"tests": len(targets)})
    return db.q("SELECT * FROM tests WHERE release=?", (rid,))


def run_agent(release: dict) -> dict:
    from . import staging
    from .releases import create_bug

    project, rid = release["project"], release["id"]
    proj = db.one("SELECT * FROM projects WHERE id=?", (project,))
    out = {"ran": 0, "passed": 0, "failed": 0, "manual": db.one("SELECT count(*) n FROM tests WHERE release=? AND owner_kind='human' AND status IN ('todo','running')", (rid,))["n"]}
    for t in db.q("SELECT * FROM tests WHERE release=? AND owner_kind='agent' AND status IN ('todo','failed')", (rid,)):
        db.x("UPDATE tests SET status='running' WHERE id=?", (t["id"],))
        target, kind = t["target"], t["kind"]
        evidence, failed, summary, title, accounts = "", False, "", "", []
        try:
            if kind.startswith("{"):
                sqls = json.loads(json.loads(kind)["sql"])
                field = target.split(":", 1)[1]
                checks = {c["sql"]: c for c in staging.field_checks(project, field)}
                parts, bad = [], []
                for sql in sqls:
                    res = staging.run(project, sql)
                    n = int(res["rows"][0][0]) if res["rows"] else 0
                    parts.append(f"{res['sql']};\n-> {n} ({res['ms']}ms)")
                    if n and sql in checks:
                        bad.append((checks[sql], n))
                failed = bool(bad)
                if bad:
                    accounts = staging.sample_accounts(project, bad[0][0])
                summary = "; ".join(f"{c['title']}: {n:,}" for c, n in bad) if bad else "No rows at risk"
                title = f"{bad[0][0]['title']}: {bad[0][1]:,} rows" + (f" (+{len(bad) - 1} more check)" if len(bad) > 1 else "") if bad else ""
                evidence = "-- staging, read-only\n" + "\n\n".join(parts) + (f"\n\nSample records: {', '.join(map(str, accounts))}" if accounts else "")
            else:
                label = (graph.node(project, target) or {"label": target.split(':', 1)[1]})["label"].split("@")[0]
                _, _, path = label.partition(" ")
                r = httpx.get(netguard.safe_http(proj["staging_url"], "staging URL").rstrip("/") + path, timeout=10)
                failed = r.status_code >= 500
                summary = f"GET {path} -> {r.status_code}"
                title = f"GET {path} returns {r.status_code} on staging"
                evidence = f"GET {proj['staging_url'].rstrip('/')}{path}\n-> {r.status_code}\n{r.text[:1500]}"
        except Exception as e:
            failed, summary, title, evidence = True, f"check failed to run: {type(e).__name__}", f"{t['charter']} could not run", str(e)[:1500]
        out["ran"] += 1
        out["failed" if failed else "passed"] += 1
        db.x("UPDATE tests SET status=?, result=?, run_at=? WHERE id=?", ("failed" if failed else "passed", summary, time.time(), t["id"]))
        attach_text(project, "test", t["id"], "agent-evidence.txt", evidence, "Agent tester", "agent")
        if t["risk"]:
            db.x("UPDATE risks SET status=?, tested_by='agent-tester', tested_at=? WHERE id=?", ("failed" if failed else "verified", time.time(), t["risk"]))
        if failed:
            bug = create_bug(project, rid, "staging", title, f"Agent tester on staging: {summary}.", "critical", "agent-tester", "agent",
                             blocker=True, evidence=summary, links=[target])
            attach_text(project, "bug", bug["id"], "agent-evidence.txt", evidence, "Agent tester", "agent")
            db.x("UPDATE tests SET bug=? WHERE id=?", (bug["id"], t["id"]))
    db.event(project, rid, "agent-tester", "run", out)
    return out


# ---------------- humans ----------------

def claim(test_id: str, user: str):
    from .pipeline import name
    t = db.one("SELECT * FROM tests WHERE id=?", (test_id,))
    db.x("UPDATE tests SET claimed_by=?, status='running' WHERE id=?", (user, test_id))
    note(t["project"], "test", test_id, name(user), "human", "On it.")


def finish(test_id: str, user: str, status: str, notes_text: str, steps: list[str] | None, save_recipe: bool) -> dict | None:
    from .pipeline import name
    from .releases import create_bug

    t = db.one("SELECT * FROM tests WHERE id=?", (test_id,))
    db.x("UPDATE tests SET status=?, result=?, by=?, run_at=?, claimed_by=COALESCE(claimed_by, ?) WHERE id=?",
         (status, notes_text or f"{status} by {name(user)}", user, time.time(), user, test_id))
    if steps:
        db.x("UPDATE tests SET how=? WHERE id=?", (json.dumps(steps), test_id))
    if notes_text:
        note(t["project"], "test", test_id, name(user), "human", notes_text)
    if save_recipe:
        db.x("INSERT INTO recipes VALUES(?,?,?,?,?,?,?,?)", (db.uid("rcp"), t["project"], t["target"], t["charter"],
                                                            json.dumps(steps or json.loads(t["how"] or "[]")), "human", name(user), time.time()))
        note(t["project"], "test", test_id, "Agent tester", "agent",
             f"Saved. Next time I'll use {name(user).split()[0]}'s steps for this, and say they came from them.")
    if t["risk"]:
        db.x("UPDATE risks SET status=?, tested_by=?, tested_at=? WHERE id=?", ("verified" if status == "passed" else "failed", user, time.time(), t["risk"]))
    bug = None
    if t["bug"] and status == "passed":  # a human reproduce request that came back clean
        pass
    elif t["bug"]:  # human reproduced a bug the agent couldn't
        from .pipeline import relay
        relay(t["bug"], "reproduced", name(user), "human", notes_text or "Reproduced by hand, recording attached")
        for a in db.q("SELECT * FROM attachments WHERE owner_type='test' AND owner_id=?", (test_id,)):
            db.x("INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?,?,?,?)", (db.uid("att"), a["project"], "bug", t["bug"], a["kind"], a["name"],
                                                                          a["path"], a["text"], a["by"], a["by_kind"], time.time()))
    elif status == "failed":
        bug = create_bug(t["project"], t["release"], "staging", t["charter"] + " failed", notes_text or "Found during a manual check.",
                         "major", user, "human", blocker=False, links=[t["target"]])
        for a in db.q("SELECT * FROM attachments WHERE owner_type='test' AND owner_id=?", (test_id,)):
            db.x("INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?,?,?,?)", (db.uid("att"), a["project"], "bug", bug["id"], a["kind"], a["name"],
                                                                          a["path"], a["text"], a["by"], a["by_kind"], time.time()))
        db.x("UPDATE tests SET bug=? WHERE id=?", (bug["id"], test_id))
    db.event(t["project"], t["release"], user, "test_finished", {"test": test_id, "status": status})
    return bug


def ask_human_repro(project: str, bug: dict, why: str):
    """The repro agent couldn't reproduce: hand it to a tester with everything it knows."""
    from .pipeline import name
    tester = _least_busy_tester(project)
    tid = db.uid("test")
    rp = json.loads(bug.get("repro") or "{}")
    steps = [s["do"] + (f": {s['detail']}" if s.get("detail") else "") for s in rp.get("steps", [])] or ["Follow the reporter's steps"]
    db.x("""INSERT INTO tests(id, project, release, risk, charter, kind, target, status, by, result, owner_kind, claimed_by, how, source, help, bug, created)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
         (tid, project, bug["release"], None, f"Reproduce: {bug['title']}", "manual", f"bug:{bug['id']}", "todo", None, "", "human",
          tester, json.dumps(steps), "drafted by the repro agent", why, bug["id"], time.time()))
    msg = f"I couldn't reproduce this on my own: {why} My draft steps are on the test. " + \
          (f"@{name(tester).split()[0]}, can you try it and record your screen?" if tester else "Can a tester try it?")
    note(project, "bug", bug["id"], "Repro agent", "agent", msg, ask=tester)
    note(project, "test", tid, "Repro agent", "agent", msg, ask=tester)
    return tid


def session(release: dict) -> dict:
    from .pipeline import name
    tests = db.q("SELECT * FROM tests WHERE release=? ORDER BY created", (release["id"],))
    out = []
    for t in tests:
        n_ev = db.one("SELECT count(*) n FROM attachments WHERE owner_type='test' AND owner_id=?", (t["id"],))["n"]
        out.append({"id": t["id"], "charter": t["charter"], "target": t["target"], "status": t["status"], "owner_kind": t["owner_kind"],
                    "claimed_by": t["claimed_by"], "claimed_name": name(t["claimed_by"]), "source": t["source"], "help": t["help"],
                    "result": t["result"], "bug": t["bug"], "evidence": n_ev,
                    "unread_ask": bool(db.one("SELECT 1 FROM notes WHERE owner_type='test' AND owner_id=? AND author_kind='agent' AND ask IS NOT NULL", (t["id"],)))})
    testers = db.q("SELECT u.id, u.name, u.shift FROM members m JOIN users u ON u.id=m.user WHERE m.project=? AND m.role='tester'", (release["project"],))
    return {"tests": out, "testers": testers, "recipes": db.one("SELECT count(*) n FROM recipes WHERE project=?", (release["project"],))["n"]}
