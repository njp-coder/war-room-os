"""Seed a demo agency org with the Lumen project, so every screen works before a real repo is connected.
All names and data are fictional demo data."""
from __future__ import annotations

import json
import time

from . import db, graph

ORG = "org_northstar"


def seed(world):
    if db.one("SELECT 1 FROM orgs WHERE id=?", (ORG,)):
        return
    now = time.time()
    db.x("INSERT INTO orgs VALUES(?,?,?)", (ORG, "Northstar Studio (demo)", "agency"))
    db.xmany("INSERT INTO clients VALUES(?,?,?)", [("cl_lumen", ORG, "Lumen"), ("cl_harbour", ORG, "Harbourline")])

    users = [("you", "You", "you@northstar.dev", "War room lead", "admin", "on", 9, 1, None)]
    for p in world.d["people"]:
        if p["id"] == "you":
            continue
        users.append((p["id"], p["name"], f"{p['id']}@northstar.dev", p["role"], "member", p["shift"], p["hours_today"],
                      int(p["client_facing"]), p["id"]))
    users += [("ishita", "Ishita Ghosh", "ishita@northstar.dev", "QA tester", "member", "on", 5, 0, "ishita"),
              ("pranav", "Pranav Joshi", "pranav@northstar.dev", "QA tester", "member", "on", 3, 0, "pranav"),
              ("guest_kestrel", "Kestrel Fitness (client)", "ops@kestrel.example", "Client guest", "guest", "on", 0, 0, None)]
    db.xmany("INSERT INTO users(id, org, name, email, title, org_role, shift, hours_today, client_facing, github) VALUES(?,?,?,?,?,?,?,?,?,?)",
             [(u[0], ORG, *u[1:]) for u in users])

    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?)",
         ("demo", ORG, "cl_lumen", "Lumen v2 platform", "Consumer subscription app moving from a legacy monolith to v2.",
          now, 1, None, json.dumps({"client_facing": True})))
    roles = {"arjun": "owner", "you": "lead", "meera": "engineer", "rohan": "engineer", "farhan": "engineer", "sameer": "engineer",
             "divya": "engineer", "tanvi": "engineer", "vikram": "engineer", "anjali": "support", "kavya": "support",
             "nisha": "support", "ishita": "tester", "pranav": "tester", "guest_kestrel": "client_guest"}
    db.xmany("INSERT INTO members(project, user, role) VALUES(?,?,?)", [("demo", u, r) for u, r in roles.items()])
    db.x("INSERT INTO repos VALUES(?,?,?,?,?,?)", ("demo", "northstar/lumen (demo)", "main", "ready",
                                                    json.dumps({l: {"state": "done", "detail": "demo data"} for l in
                                                                ["clone", "structure", "data", "service", "history", "people", "docs"]}), now))

    # graph from the Lumen dataset
    nodes, edges = [], []
    for path in world.d["code"]:
        nodes.append((f"file:lumen:{path}", "file", path, {"repo": "lumen"}))
    for m in world.d["mapping"]:
        nodes.append((f"field:{m['field']}", "field", m["field"], {"nullable": m["nullable_legacy"], "v2": m["v2"]}))
    for c in world.d["commits"]:
        pid = f"pr:lumen#{c['pr']}"
        nodes.append((pid, "pr", f"#{c['pr']} {c['title']}", {"state": "closed", "milestone": "v2 migration" if c["minute"] < 0 else "hotfixes",
                                                               "labels": ["migration"], "url": ""}))
        nodes += [(f"person:{c['author']}", "person", c["author"], {}), (f"person:{c['reviewer']}", "person", c["reviewer"], {})]
        edges += [(f"person:{c['author']}", pid, "authored", ""), (f"person:{c['reviewer']}", pid, "reviewed", "")]
        for f in c["files"]:
            edges.append((pid, f"file:lumen:{f}", "touches", ""))
            for fld in c["fields"]:
                edges.append((f"file:lumen:{f}", f"field:{fld}", "alters", pid))
    graph.add_nodes("demo", nodes)
    graph.add_edges("demo", edges)

    # releases at different stages
    rel = [("rel_v2", "v2 migration", "staging", "2026-09-24T22:00", "2026-09-25T02:00", "milestone", "v2 migration", 1,
            "Feature flag v2_read off, re-point reads to legacy, replay writes from the outbox."),
           ("rel_theme", "Theme refresh", "plan", "2026-10-08T21:00", "2026-10-08T22:00", "label", "theme", 0, ""),
           ("rel_aug", "August billing patch", "closed", "2026-08-12T21:00", "2026-08-12T22:00", "label", "billing", 0, "Revert deploy.")]
    db.xmany("INSERT INTO releases(id, project, name, stage, window_start, window_end, scope_type, scope_value, migration, rollback_plan, created) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?)", [(r[0], "demo", *r[1:], now) for r in rel])
    db.x("UPDATE releases SET postmortem=?, closed=? WHERE id='rel_aug'",
         ("# Postmortem: August billing patch\n\nBugs: 2 total, 2 caught on staging, 0 in production.\n\n## Learnings\nAnchor dates must be computed in the account timezone.", now))
    from .ingest import risk_map
    risks = [(f"rel_v2:risk:{r['field']}:{i}", "demo", "rel_v2", f"field:{r['field']}", r["kind"],
              "high" if ("required" in r["kind"] or "timezone" in r["kind"] or "active" in r["kind"]) else "medium", r["note"])
             for i, r in enumerate(risk_map(world))]
    db.xmany("INSERT INTO risks(id, project, release, target, kind, level, note) VALUES(?,?,?,?,?,?,?)", risks)
    db.event("demo", "rel_v2", "risk-agent", "premortem", {"risks": len(risks)})
    # one human-reported staging bug so the board isn't empty
    from .releases import create_bug
    create_bug("demo", "rel_v2", "staging", "Invoice PDF shows the wrong renewal date for Singapore test account",
               "Test account sg-annual-3: renewal shows Aug 31, legacy shows Sep 1.", "major", "ishita", "human")
