"""The context map: a project grouped into the areas people actually talk about (Orders, Kitchens, Coupons...).

An area is the route file its endpoints live in (routes/orders.py -> Orders), or, when that file name is generic
(main.py, route.ts), the first path segment after prefixes every route shares (like /api/v1).
For each area, the chain an incident travels: requests -> code that handles them (handlers plus what they call) ->
tables they read and write -> the changes that touched that code -> the people who made them.
Everything comes from the graph the sync built; nothing is guessed.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict

from . import db, graph

WEEKS = 14
SKIP_SEG = re.compile(r"^(api|v\d+|internal|public)$", re.I)
GENERIC = {"main", "app", "api", "route", "routes", "router", "routers", "views", "view", "index", "server", "urls", "__init__",
           "handlers", "handler", "controllers", "controller", "endpoints", "deps"}


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s and not s.startswith("{") and not s.startswith(":") and not s.startswith("<")]


def _area_key(path: str, shared: int) -> str:
    segs = _segments(path)[shared:]
    segs = [s for s in segs if not SKIP_SEG.match(s)]
    return segs[0].lower() if segs else "root"


def _title(key: str) -> str:
    return "Root" if key == "root" else " ".join(w.capitalize() for w in re.split(r"[-_]", key))


def _is_test(file_id: str) -> bool:
    return bool(re.search(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_|_test\.|\.spec\.|\.test\.", file_id))


def build(project: str) -> dict:
    eps = db.q("SELECT id, label, props FROM nodes WHERE project=? AND type='endpoint'", (project,))
    if not eps:
        return {"areas": [], "stats": {}, "gaps": ["No HTTP endpoints found in the code, so there are no areas to map yet."], "people": 0}
    paths = [e["label"].split(" ", 1)[1] if " " in e["label"] else e["label"] for e in eps]
    # segments every endpoint shares (e.g. api/v1) don't name an area
    shared = 0
    segsets = [_segments(p) for p in paths]
    while all(len(s) > shared for s in segsets) and len({s[shared] for s in segsets}) == 1 and len(segsets) > 1:
        shared += 1
    groups: dict[str, list[dict]] = defaultdict(list)
    for e, p in zip(eps, paths):
        # Teams organise routes by file (routes/orders.py); use that name unless it's generic (main.py, route.ts).
        f = json.loads(e["props"] or "{}").get("file", "")
        stem = re.sub(r"\.(py|ts|tsx|js|jsx|mjs|go|rb)$", "", f.split("/")[-1]).lower()
        groups[stem if stem and stem not in GENERIC and not stem.startswith("[") else _area_key(p, shared)].append(e)

    pr_nodes = {n["id"]: json.loads(n["props"] or "{}") for n in db.q("SELECT id, props FROM nodes WHERE project=? AND type='pr'", (project,))}
    now = time.time()
    all_authors: set[str] = set()
    areas = []
    for key, members in groups.items():
        handlers = []
        for e in members:
            handlers += [h["id"] for h in graph.neighbors(project, e["id"], {"handled_by"}, "out")]
        # code: handlers plus what they call, two hops, same repo, no tests
        code, frontier = set(handlers), list(handlers)
        for _ in range(2):
            nxt = []
            for s in frontier:
                for c in graph.neighbors(project, s, {"calls"}, "out"):
                    if c["id"].startswith("sym:") and c["id"] not in code and not _is_test(c["id"]):
                        code.add(c["id"])
                        nxt.append(c["id"])
            frontier = nxt
        files = {"file:" + s[4:].split("#")[0] for s in code}
        tables: Counter = Counter()
        columns: set[str] = set()
        writes: set[str] = set()
        for s in code:
            for f in graph.neighbors(project, s, {"uses_field", "writes_field"}, "out"):
                fld = f["id"].split(":", 1)[1]
                tables[fld.split(".")[0]] += 1
                columns.add(fld)
                if f["edge"] == "writes_field":
                    writes.add(fld.split(".")[0])
        prs: set[str] = set()
        for f in files:
            prs |= {p["id"] for p in graph.neighbors(project, f, {"touches"}, "in")}
        authors: Counter = Counter()
        reviewers: Counter = Counter()
        heat = [0] * WEEKS
        for p in prs:
            for a in graph.neighbors(project, p, {"authored"}, "in"):
                authors[a["id"].split(":", 1)[1]] += 1
            for r in graph.neighbors(project, p, {"reviewed"}, "in"):
                reviewers[r["id"].split(":", 1)[1]] += 1
            merged = (pr_nodes.get(p) or {}).get("merged_at")
            if merged:
                try:
                    t = time.mktime(time.strptime(merged[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
                    wk = int((now - t) // (7 * 86400))
                    if 0 <= wk < WEEKS:
                        heat[WEEKS - 1 - wk] += 1
                except ValueError:
                    pass
        all_authors |= set(authors)
        people = []
        for login, n in authors.most_common(4):
            u = db.one("SELECT u.id, u.name FROM users u JOIN members m ON m.user=u.id AND m.project=? WHERE u.github=?", (project, login))
            people.append({"login": login, "name": u["name"] if u else login, "member": bool(u), "prs": n})
        handler_names = [h.split("#")[-1] for h in handlers]
        areas.append({
            "id": key, "name": _title(key),
            "endpoints": sorted({e["label"] for e in members}),
            "handlers": handler_names[:6],
            "functions": len(code), "files": len(files),
            "tables": [t for t, _ in tables.most_common(6)], "writes": sorted(writes), "columns": len(columns),
            "prs": len(prs), "heat": heat,
            "people": people, "reviewers": [r for r, _ in reviewers.most_common(3)],
            "single_owner": len(authors) == 1,
            "file_paths": sorted(f.split(":", 2)[-1] for f in files),
            "node_ids": {"endpoints": [e["id"] for e in members], "handlers": handlers[:6],
                         "tables": [f"table:{t}" for t, _ in tables.most_common(6)]},
        })
    areas.sort(key=lambda a: -(a["functions"] + len(a["endpoints"])))

    gaps = []
    unhandled = [e["label"] for e in eps if not graph.neighbors(project, e["id"], {"handled_by"}, "out")]
    if unhandled:
        gaps.append({"title": f"{len(unhandled)} endpoint{'s' if len(unhandled) > 1 else ''} with no handler found",
                     "detail": ", ".join(unhandled[:4]) + ". Agents can't follow these requests into code."})
    unresolved = [json.loads(e["props"] or "{}").get("unresolved_prefix") for e in eps]
    unresolved = [u for u in unresolved if u]
    if unresolved:
        gaps.append({"title": f"{len(unresolved)} endpoint{'s' if len(unresolved) > 1 else ''} with an unknown path prefix",
                     "detail": f"Mounted under {unresolved[0]}, which isn't a constant in the code. Their paths may not match production requests."})
    solo = [a["name"] for a in areas if a["single_owner"] and a["prs"]]
    counts = {r["type"]: r["n"] for r in db.q("SELECT type, count(*) n FROM nodes WHERE project=? GROUP BY type", (project,))}
    stats = {"endpoints": counts.get("endpoint", 0), "functions": counts.get("symbol", 0), "files": counts.get("file", 0),
             "tables": counts.get("table", 0), "columns": counts.get("field", 0), "prs": counts.get("pr", 0),
             "people": len(all_authors), "solo_areas": solo}
    return {"areas": areas, "stats": stats, "gaps": gaps}
