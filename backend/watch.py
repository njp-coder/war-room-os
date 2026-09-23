"""Watch after release: a configurable window after go-live where monitoring focuses on what the release changed.

Per release: how long (hours) and what to watch. Targets are suggested from the release's actual diff:
  endpoint -> requests that reach changed code
  table    -> tables the changed code or its migrations read and write
  file     -> changed code files, so errors whose stack traces land there count
People can switch targets off or add their own. At go-live, the baseline is taken from the last 24 h of monitoring
signals, then each monitoring check compares the watched queries and error patterns with it. A regression during the window
raises a war room tied to the release without waiting for the usual sustain. When the window ends, a before/after summary.
"""
from __future__ import annotations

import json
import re
import time

from . import db, graph

db.x("""CREATE TABLE IF NOT EXISTS release_watch(release TEXT PRIMARY KEY, project TEXT, hours REAL, targets TEXT, settings TEXT,
        status TEXT, started_at REAL, ends_at REAL, baseline TEXT, findings TEXT DEFAULT '[]', summary TEXT)""")

DEFAULTS = {"hours": 24, "regression_factor": 2.0, "min_ms": 50, "new_error_min": 1}


# ---------------- what to watch ----------------

def suggest(release: dict) -> list[dict]:
    from .releases import changed_symbols, linked_prs
    project = release["project"]
    changed = changed_symbols(release)
    out: dict[str, dict] = {}
    code_syms: set[str] = set()
    for fid, syms in changed.items():
        path = fid.split(":", 2)[-1]
        if re.search(r"(^|/)(tests?|__tests__|docs?)/|\.(md|txt|json|ya?ml|lock|css)$", path):
            continue
        code_syms |= syms
        if re.search(r"\.(py|ts|tsx|js|jsx|go|rb|java|kt|cs|php)$", path) and not re.search(r"(^|/)(migrations|alembic/versions)/", path):
            out[f"file:{path}"] = {"kind": "file", "id": path, "label": path.split("/")[-1], "why": "changed in this release", "on": True}
    for s in code_syms:
        # requests that reach this code: its own route, or a route whose handler calls it (two hops)
        callers, frontier = {s}, [s]
        for _ in range(2):
            nxt = [c["id"] for f in frontier for c in graph.neighbors(project, f, {"calls"}, "in")]
            frontier = [c for c in nxt if c not in callers]
            callers |= set(frontier)
        for c in callers:
            for r in graph.neighbors(project, c, {"handled_by"}, "in"):
                n = graph.node(project, r["id"]) or {"label": r["id"]}
                out[f"endpoint:{n['label']}"] = {"kind": "endpoint", "id": r["id"], "label": n["label"].split("@")[0],
                                                 "why": f"reaches {s.split('#')[-1]}()", "on": True}
        for f in graph.neighbors(project, s, {"uses_field", "writes_field"}, "out"):
            t = f["id"].split(":", 1)[1].split(".")[0]
            out.setdefault(f"table:{t}", {"kind": "table", "id": t, "label": t,
                                          "why": f"{'written' if f['edge'] == 'writes_field' else 'read'} by {s.split('#')[-1]}()", "on": True})
    for pr in linked_prs(release):  # migrations in the release
        for e in db.q("SELECT dst FROM edges WHERE project=? AND source=? AND type='alters'", (project, pr["id"])):
            t = e["dst"].split(":", 1)[1].split(".")[0]
            out[f"table:{t}"] = {"kind": "table", "id": t, "label": t, "why": "a migration in this release changes it", "on": True}
    order = {"endpoint": 0, "table": 1, "file": 2}
    return sorted(out.values(), key=lambda x: (order[x["kind"]], x["label"]))[:40]


def get(release: dict) -> dict:
    row = db.one("SELECT * FROM release_watch WHERE release=?", (release["id"],))
    if not row:
        return {"release": release["id"], "status": "not_set", "hours": DEFAULTS["hours"], "settings": dict(DEFAULTS),
                "targets": suggest(release), "suggested": True}
    out = {**row, "targets": json.loads(row["targets"] or "[]"), "settings": {**DEFAULTS, **json.loads(row["settings"] or "{}")},
           "baseline": json.loads(row["baseline"] or "{}"), "findings": json.loads(row["findings"] or "[]"), "suggested": False}
    return out


def configure(release: dict, hours: float, targets: list[dict], settings: dict | None = None) -> dict:
    hours = max(0.5, min(float(hours), 24 * 7))
    s = {**DEFAULTS, **(settings or {}), "hours": hours}
    row = db.one("SELECT status, started_at FROM release_watch WHERE release=?", (release["id"],))
    if row and row["status"] == "watching":
        # changing a running watch: keep its start, move its end
        db.x("UPDATE release_watch SET hours=?, targets=?, settings=?, ends_at=? WHERE release=?",
             (hours, json.dumps(targets), json.dumps(s), row["started_at"] + hours * 3600, release["id"]))
    else:
        db.x("""INSERT INTO release_watch(release, project, hours, targets, settings, status) VALUES(?,?,?,?,?, 'scheduled')
                ON CONFLICT(release) DO UPDATE SET hours=excluded.hours, targets=excluded.targets, settings=excluded.settings""",
             (release["id"], release["project"], hours, json.dumps(targets), json.dumps(s)))
    return get(release)


# ---------------- baseline and comparison ----------------

def _statements_for(project: str, tables: list[str], since: float, until: float | None = None) -> dict[str, dict]:
    """Per query fingerprint touching a watched table: average ms and samples, from the monitoring signals."""
    if not tables:
        return {}
    rows = db.q("SELECT fingerprint, value, label, at FROM signals WHERE project=? AND rule='query_ms' AND at>=? AND at<?",
                (project, since, until or time.time() + 1))
    out: dict[str, dict] = {}
    for r in rows:
        label = r["label"] or ""
        hit = [t for t in tables if re.search(rf'\b(from|join|update|into)\s+"?{re.escape(t)}"?\b', label, re.I)]
        if hit:
            s = out.setdefault(r["fingerprint"], {"query": label, "tables": hit, "sum": 0.0, "n": 0})
            s["sum"] += r["value"]
            s["n"] += 1
    return {k: {"query": v["query"], "tables": v["tables"], "ms": round(v["sum"] / v["n"], 1), "samples": v["n"]} for k, v in out.items()}


def _errors_for(project: str, files: list[str], since: float, until: float | None = None) -> dict[str, dict]:
    if not files:
        return {}
    out = {}
    for p in db.q("SELECT fingerprint, template, frames, first_seen FROM patterns WHERE project=?", (project,)):
        frames = json.loads(p["frames"] or "[]")
        if any(any(str(fr[0]).endswith(f) or f.endswith(str(fr[0]).lstrip("./")) for f in files) for fr in frames):
            n = db.one("SELECT coalesce(sum(value),0) s, count(*) c FROM signals WHERE project=? AND rule='errors' AND fingerprint=? AND at>=? AND at<?",
                       (project, p["fingerprint"], since, until or time.time() + 1))
            out[p["fingerprint"]] = {"template": p["template"][:200], "count": int(n["s"]), "checks": n["c"], "first_seen": p["first_seen"]}
    return out


def start(release: dict) -> dict | None:
    """Called at go-live. Uses the configured watch, or the suggested one if nobody configured it."""
    w = get(release)
    if w["status"] == "not_set":
        w = configure(release, DEFAULTS["hours"], w["targets"])
    targets = [t for t in w["targets"] if t.get("on", True)]
    tables = [t["id"] for t in targets if t["kind"] == "table"]
    files = [t["id"] for t in targets if t["kind"] == "file"]
    now = time.time()
    baseline = {"queries": _statements_for(release["project"], tables, now - 86400, now),
                "errors": _errors_for(release["project"], files, now - 86400, now)}
    db.x("UPDATE release_watch SET status='watching', started_at=?, ends_at=?, baseline=?, findings='[]', summary=NULL WHERE release=?",
         (now, now + w["hours"] * 3600, json.dumps(baseline), release["id"]))
    db.event(release["project"], release["id"], "monitor", "watch_started",
             {"note": f"{w['hours']:g} h, {len(targets)} things: {', '.join(t['label'] for t in targets[:4])}"})
    return get(release)


def active(project: str) -> list[dict]:
    out = []
    for r in db.q("SELECT release FROM release_watch WHERE project=? AND status='watching'", (project,)):
        rel = db.one("SELECT * FROM releases WHERE id=?", (r["release"],))
        if rel:
            out.append(get(rel))
        else:  # the release was deleted: its watch must not break monitoring for everyone else
            db.x("UPDATE release_watch SET status='done', summary='Release deleted while being watched.' WHERE release=?", (r["release"],))
    return out


def breaches(project: str) -> list[dict]:
    """Release-specific checks for every active watch. Returns breaches the monitor raises straight away (no sustain)."""
    out = []
    for w in active(project):
        rel = db.one("SELECT * FROM releases WHERE id=?", (w["release"],))
        if time.time() >= (w["ends_at"] or 0):
            finish(rel)
            continue
        s = w["settings"]
        targets = [t for t in w["targets"] if t.get("on", True)]
        tables = [t["id"] for t in targets if t["kind"] == "table"]
        files = [t["id"] for t in targets if t["kind"] == "file"]
        base_q, base_e = w["baseline"].get("queries", {}), w["baseline"].get("errors", {})
        now_q = _statements_for(project, tables, max(w["started_at"], time.time() - 600))  # last 10 minutes
        for fp, cur in now_q.items():
            b = base_q.get(fp)
            if b and cur["ms"] >= s["min_ms"] and cur["ms"] >= s["regression_factor"] * max(b["ms"], 1):
                out.append({"rule": "release_regression", "fingerprint": f"rel:{rel['id']}:{fp}", "severity": "high" if cur["ms"] >= 4 * b["ms"] else "medium",
                            "value": cur["ms"], "baseline": b["ms"], "query": cur["query"], "release": rel["id"],
                            "title": f"After {rel['name']}: a query on {', '.join(cur['tables'])} went from {b['ms']:g} ms to {cur['ms']:g} ms"})
            elif not b and cur["ms"] >= max(s["min_ms"] * 4, 300) and cur["samples"] >= 2:
                out.append({"rule": "release_new_slow_query", "fingerprint": f"rel:{rel['id']}:{fp}", "severity": "medium",
                            "value": cur["ms"], "baseline": None, "query": cur["query"], "release": rel["id"],
                            "title": f"After {rel['name']}: a new query on {', '.join(cur['tables'])} takes {cur['ms']:g} ms"})
        for fp, cur in _errors_for(project, files, w["started_at"]).items():
            b = base_e.get(fp)
            new = (cur["first_seen"] or 0) >= w["started_at"] - 60
            if (new or not b) and cur["count"] >= s["new_error_min"]:
                p = db.one("SELECT sample, frames, link, source FROM patterns WHERE project=? AND fingerprint=?", (project, fp)) or {}
                out.append({"rule": "release_new_error", "fingerprint": f"rel:{rel['id']}:{fp}", "severity": "high", "value": cur["count"],
                            "template": cur["template"], "sample": p.get("sample"), "frames": json.loads(p.get("frames") or "[]"),
                            "link": p.get("link"), "release": rel["id"],
                            "title": f"After {rel['name']}: new error in changed code: {cur['template'][:90]}"})
    return out


def record(release_id: str, breach: dict, incident: str | None, action: str):
    row = db.one("SELECT findings FROM release_watch WHERE release=?", (release_id,))
    f = json.loads(row["findings"] or "[]") if row else []
    f.append({"at": time.time(), "rule": breach["rule"], "title": breach["title"], "incident": incident, "action": action})
    db.x("UPDATE release_watch SET findings=? WHERE release=?", (json.dumps(f[-50:]), release_id))


def finish(release: dict, reason: str = "window ended") -> dict:
    w = get(release)
    if w["status"] != "watching":
        return w
    targets = [t for t in w["targets"] if t.get("on", True)]
    tables = [t["id"] for t in targets if t["kind"] == "table"]
    after = _statements_for(release["project"], tables, w["started_at"])
    compare = []
    for fp, b in w["baseline"].get("queries", {}).items():
        a = after.get(fp)
        if a:
            compare.append({"query": b["query"][:120], "before_ms": b["ms"], "after_ms": a["ms"]})
    regressions = [c for c in compare if c["after_ms"] >= w["settings"]["regression_factor"] * max(c["before_ms"], 1) and c["after_ms"] >= w["settings"]["min_ms"]]
    findings = w["findings"]
    hours = (time.time() - w["started_at"]) / 3600
    if findings or regressions:
        summary = (f"Watched {hours:.1f} h ({reason}). {len(findings)} problem{'s' if len(findings) != 1 else ''} raised"
                   + (f", {len(regressions)} quer{'ies' if len(regressions) != 1 else 'y'} still slower than before" if regressions else "") + ".")
    else:
        summary = f"Watched {hours:.1f} h ({reason}). No regressions: {len(compare)} watched quer{'ies' if len(compare) != 1 else 'y'} as fast as before, no new errors in changed code."
    db.x("UPDATE release_watch SET status='done', summary=?, baseline=? WHERE release=?",
         (summary, json.dumps({**w["baseline"], "after": after, "compare": compare}), release["id"]))
    db.event(release["project"], release["id"], "monitor", "watch_done", {"note": summary})
    return get(release)
