"""Typed project graph with time on every edge. Node IDs equal Moss card IDs."""
from __future__ import annotations

import json
import time
from collections import deque

from . import db


def add_nodes(project: str, nodes: list[tuple[str, str, str, dict]]):
    db.xmany("INSERT OR REPLACE INTO nodes VALUES(?,?,?,?,?)",
             [(project, nid, ntype, label, json.dumps(props)) for nid, ntype, label, props in nodes])


def add_edges(project: str, edges: list[tuple[str, str, str, str]], valid_from: float | None = None):
    now = valid_from or time.time()
    db.xmany("INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?,?,?)",
             [(project, s, d, t, now, None, src) for s, d, t, src in edges])


def close_edges_from(project: str, src_prefix: str):
    """A file changed: its old outgoing edges stop being valid (history is kept)."""
    db.x("UPDATE edges SET valid_to=? WHERE project=? AND src LIKE ? AND valid_to IS NULL", (time.time(), project, src_prefix + "%"))


def node(project: str, nid: str) -> dict | None:
    n = db.one("SELECT id, type, label, props FROM nodes WHERE project=? AND id=?", (project, nid))
    if n:
        n["props"] = json.loads(n["props"] or "{}")
    return n


def neighbors(project: str, nid: str, types: set[str] | None = None, direction: str = "both") -> list[dict]:
    rows = []
    if direction in ("out", "both"):
        rows += [{"id": r["dst"], "edge": r["type"], "dir": "out"} for r in db.q(
            "SELECT dst, type FROM edges WHERE project=? AND src=? AND valid_to IS NULL", (project, nid))]
    if direction in ("in", "both"):
        rows += [{"id": r["src"], "edge": r["type"], "dir": "in"} for r in db.q(
            "SELECT src, type FROM edges WHERE project=? AND dst=? AND valid_to IS NULL", (project, nid))]
    return [r for r in rows if not types or r["edge"] in types]


# Edges that carry impact from a changed thing to what depends on it.
# Edge directions: file-defines->symbol, symbol-calls->symbol, file-imports->file, symbol-uses_field->field,
# symbol-writes_field->field, endpoint-handled_by->symbol, pr-touches->file, file-alters->field.
IMPACT = {
    ("out", "touches"),                      # PR -> files it changed
    ("out", "defines"),                      # file -> its symbols
    ("in", "imports"),                       # file -> files that import it
    ("in", "calls"),                         # symbol -> its callers
    ("in", "handled_by"),                    # symbol -> endpoints it serves
    ("out", "uses_field"), ("out", "writes_field"), ("out", "alters"),   # code/migration -> fields
    ("in", "uses_field"), ("in", "writes_field"),                        # field -> other code using it
}


def blast_radius(project: str, seeds: list[str], max_depth: int = 3, limit: int = 250) -> dict:
    """BFS from changed nodes along impact edges. Returns reached nodes grouped by type, plus paths."""
    seen = {s: (0, None, None) for s in seeds}
    queue = deque((s, 0) for s in seeds)
    while queue and len(seen) < limit:
        cur, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for nb in neighbors(project, cur):
            if (nb["dir"], nb["edge"]) not in IMPACT or nb["id"] in seen:
                continue
            seen[nb["id"]] = (depth + 1, cur, nb["edge"])
            queue.append((nb["id"], depth + 1))
    ids = list(seen)
    info = {n["id"]: n for n in db.q(
        f"SELECT id, type, label FROM nodes WHERE project=? AND id IN ({','.join('?' * len(ids))})", (project, *ids))} if ids else {}
    groups: dict[str, list] = {}
    for nid, (depth, parent, edge) in seen.items():
        n = info.get(nid, {"id": nid, "type": nid.split(":")[0], "label": nid})
        groups.setdefault(n["type"], []).append({"id": nid, "label": n["label"], "depth": depth, "via": parent, "edge": edge})
    for g in groups.values():
        g.sort(key=lambda r: r["depth"])
    return {"seeds": seeds, "groups": groups, "size": len(seen)}


def path_to(project: str, start: str, goal_types: set[str], max_depth: int = 4) -> list[dict]:
    """Shortest path from a node to the nearest node of one of goal_types (evidence chains)."""
    prev = {start: None}
    queue = deque([(start, 0)])
    while queue:
        cur, d = queue.popleft()
        n = node(project, cur)
        if n and n["type"] in goal_types and cur != start:
            chain = []
            while cur:
                chain.append(cur)
                cur = prev[cur][0] if prev[cur] else None
            return [{"id": c, **(node(project, c) or {})} for c in reversed(chain)]
        if d >= max_depth:
            continue
        for nb in neighbors(project, cur):
            if nb["id"] not in prev:
                prev[nb["id"]] = (cur, nb["edge"])
                queue.append((nb["id"], d + 1))
    return []


def stats(project: str) -> dict:
    return {
        "nodes": {r["type"]: r["n"] for r in db.q("SELECT type, count(*) n FROM nodes WHERE project=? GROUP BY type", (project,))},
        "edges": {r["type"]: r["n"] for r in db.q("SELECT type, count(*) n FROM edges WHERE project=? AND valid_to IS NULL GROUP BY type", (project,))},
    }
