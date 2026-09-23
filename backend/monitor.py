"""Monitoring agent: watches production database performance, opens (or proposes) war rooms within guardrails.

Reads only performance views over a read-only connection ("monitor_db"):
  Postgres: pg_stat_statements, pg_stat_activity, pg_stat_database, pg_settings
  MySQL:    performance_schema.events_statements_summary_by_digest, information_schema.processlist, global status
Rules compare each sample with the previous one (deltas) and with a per-query baseline.
Guardrails: sustain N samples, cooldown per fingerprint, max auto incidents per hour, and a mode per project:
  suggest   -> the agent proposes, a person opens
  auto_low  -> the agent opens medium/low on its own, proposes high
  auto      -> the agent opens everything
With Jev on, each sustained breach also gets P(real incident): auto modes open only above jev_open_min, anything below
jev_hold_below is held back (recorded, not raised), and every probability is shown on the war room.
The agent never remediates. Killing queries, adding indexes and rollbacks are proposals for people.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time

import sqlglot
from sqlglot import exp

from . import db, graph, staging

DEFAULTS = {
    "enabled": True, "mode": "suggest", "interval_s": 30, "sustain": 2, "cooldown_min": 30, "max_auto_per_hour": 3,
    "slow_ms": 300, "slow_factor": 3.0, "spike_factor": 10.0, "min_calls": 3, "long_running_s": 20, "blocked_sessions": 3, "conn_ratio": 0.85,
    "rollbacks_per_min": 30, "errors_min": 20, "errors_factor": 5.0, "new_error_min": 10, "errors_high": 200,
    # Jev: calibrated "is this real?" on top of the rules. Auto-open only when sure enough; hold back what's likely noise.
    "jev": True, "jev_open_min": 0.85, "jev_hold_below": 0.15, "jev_recheck_min": 5,
}
_jev_seen: dict[str, dict[str, tuple[float, dict | None]]] = {}
_prev: dict[str, dict] = {}
_baseline: dict[str, dict[str, float]] = {}
_breaches: dict[str, dict[str, int]] = {}


def settings(project: str) -> dict:
    p = db.one("SELECT settings FROM projects WHERE id=?", (project,))
    s = json.loads(p["settings"] or "{}") if p else {}
    return {**DEFAULTS, **(s.get("monitor") or {})}


def save_settings(project: str, patch: dict):
    p = db.one("SELECT settings FROM projects WHERE id=?", (project,))
    s = json.loads(p["settings"] or "{}")
    s["monitor"] = {**settings(project), **{k: v for k, v in patch.items() if k in DEFAULTS}}
    db.x("UPDATE projects SET settings=? WHERE id=?", (json.dumps(s), project))


def _q(project: str, sql: str) -> list[dict]:
    res = staging.run(project, sql, kind="monitor_db")
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


def snapshot(project: str) -> dict:
    a = staging.adapter(project, "monitor_db")
    snap = {"at": time.time(), "dialect": a.kind, "statements": {}, "active": [], "blocked": 0, "conn": 0, "max_conn": 0, "rollbacks": 0}
    perf = a.perf()
    if perf is None:
        snap["unsupported"] = f"No performance views for {a.kind}. Connect its metrics through Datadog or Grafana as a source."
    else:
        snap.update(perf)
    return snap


def _fp(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


def evaluate(project: str, snap: dict) -> list[dict]:
    """Rules over the delta since the previous snapshot. Returns breaches (not yet guarded)."""
    cfg = settings(project)
    prev = _prev.get(project)
    _prev[project] = snap
    out = []
    if prev:
        minutes = max((snap["at"] - prev["at"]) / 60, 0.5)  # rates need at least a 30 s window
        base = _baseline.setdefault(project, {})
        for qid, st in snap["statements"].items():
            p = prev["statements"].get(qid)
            if not p:
                continue
            dcalls = st["calls"] - p["calls"]
            if dcalls <= 0:
                continue
            mean = (st["total_ms"] - p["total_ms"]) / dcalls
            b = base.get(qid)
            db.x("INSERT INTO signals VALUES(?,?,?,?,?,?)", (project, snap["at"], "query_ms", qid, mean, st["query"][:200]))
            slow = mean >= cfg["slow_ms"] and (b is None or mean >= cfg["slow_factor"] * b)
            spike = b is not None and b > 0 and mean >= cfg["spike_factor"] * b and mean >= cfg["slow_ms"] / 3
            if dcalls >= cfg["min_calls"] and (slow or spike):
                out.append({"rule": "slow_query", "fingerprint": qid, "severity": "high" if mean >= 4 * cfg["slow_ms"] else "medium",
                            "value": round(mean, 1), "baseline": round(b, 1) if b else None, "calls": dcalls, "query": st["query"],
                            "title": f"Slow query: {round(mean)} ms average over {dcalls} calls" + (f" ({round(mean / b, 1)}x its baseline)" if b else "")})
            else:  # only learn the baseline from healthy windows
                base[qid] = mean if b is None else 0.8 * b + 0.2 * mean
            if st.get("errors", 0) - p.get("errors", 0) >= cfg["min_calls"]:
                out.append({"rule": "failing_query", "fingerprint": qid, "severity": "high", "value": st["errors"] - p["errors"],
                            "query": st["query"], "title": f"Query failing: {st['errors'] - p['errors']} errors since last check"})
        rb = (snap["rollbacks"] - prev["rollbacks"]) / minutes
        db.x("INSERT INTO signals VALUES(?,?,?,?,?,?)", (project, snap["at"], "rollbacks_per_min", "db", rb, "rollbacks per minute"))
        if rb >= cfg["rollbacks_per_min"]:
            out.append({"rule": "failing_transactions", "fingerprint": "rollbacks", "severity": "high", "value": round(rb),
                        "title": f"{round(rb)} failed transactions per minute"})
    for a in snap["active"]:
        if a["seconds"] >= cfg["long_running_s"] and a["query"]:
            out.append({"rule": "long_running", "fingerprint": _fp("long", re.sub(r"\d+", "?", a["query"])[:200]), "severity": "medium",
                        "value": round(a["seconds"]), "query": a["query"], "pid": a["pid"],
                        "title": f"Query running for {round(a['seconds'])} s"})
    if snap["blocked"] >= cfg["blocked_sessions"]:
        out.append({"rule": "blocked_sessions", "fingerprint": "locks", "severity": "high", "value": snap["blocked"],
                    "title": f"{snap['blocked']} sessions waiting on locks"})
    if snap["max_conn"] and snap["conn"] / snap["max_conn"] >= cfg["conn_ratio"]:
        out.append({"rule": "connections", "fingerprint": "connections", "severity": "high", "value": snap["conn"],
                    "title": f"Connections at {snap['conn']} of {snap['max_conn']}"})
    return out


def evaluate_logs(project: str, window: list[dict]) -> list[dict]:
    """Error patterns from log sources: spikes against each pattern's baseline, and new patterns arriving in volume."""
    cfg = settings(project)
    base = _baseline.setdefault(project, {})
    out = []
    seen = {w["fingerprint"] for w in window}
    for fp in [k for k in base if k.startswith("log:") and k not in seen]:
        base[fp] = 0.8 * base[fp]  # quiet windows pull the baseline down
    for w in window:
        fp, n, b = w["fingerprint"], w["count"], base.get(w["fingerprint"])
        db.x("INSERT INTO signals VALUES(?,?,?,?,?,?)", (project, time.time(), "errors", fp, n, w["template"][:200]))
        ev = {"template": w["template"], "sample": w["sample"], "frames": w["frames"], "link": w["link"], "source_kind": w["source_kind"], "calls": n}
        first = db.one("SELECT first_seen FROM patterns WHERE project=? AND fingerprint=?", (project, fp))
        recent = first is not None and time.time() - first["first_seen"] < 3600
        if n >= cfg["errors_high"]:
            out.append({"rule": "error_volume", "fingerprint": fp, "severity": "high", "value": n,
                        "title": f"High error volume: {w['template'][:90]} ({n} in the last check)", **ev})
        elif recent and n >= cfg["new_error_min"]:
            out.append({"rule": "new_error", "fingerprint": fp, "severity": "high" if n >= 3 * cfg["new_error_min"] else "medium", "value": n,
                        "title": f"New error: {w['template'][:90]} ({n} in the last check)", **ev})
        elif b is not None and n >= cfg["errors_min"] and n >= cfg["errors_factor"] * max(b, 1):
            out.append({"rule": "error_spike", "fingerprint": fp, "severity": "high" if n >= 5 * cfg["errors_min"] else "medium", "value": n,
                        "baseline": round(b, 1), "title": f"Error spike: {w['template'][:80]} ({n} vs about {round(b)} normally)", **ev})
        else:
            base[fp] = n if b is None else 0.8 * b + 0.2 * n
    return out


def guard(project: str, breaches: list[dict]) -> list[tuple[dict, str]]:
    """Apply the guardrails. Returns (breach, action) with action 'open', 'propose' or 'hold'.
    Each breach that gets this far carries breach["jev"] = {"real": p, "impact": p} when Jev answered."""
    cfg = settings(project)
    seen = _breaches.setdefault(project, {})
    current = {b["fingerprint"] for b in breaches}
    for fp in list(seen):
        if fp not in current:
            seen.pop(fp)  # breach cleared: sustain counter resets
    actions = []
    opened_last_hour = db.one("SELECT count(*) n FROM incidents WHERE project=? AND trigger='agent' AND opened_at > ?",
                              (project, time.time() - 3600))["n"]
    for b in breaches:
        seen[b["fingerprint"]] = seen.get(b["fingerprint"], 0) + 1
        if seen[b["fingerprint"]] < (1 if b.get("release") else cfg["sustain"]):  # right after a release, don't wait
            continue
        recent = db.one("SELECT id, status FROM incidents WHERE project=? AND fingerprint=? AND (status IN ('open','mitigating','proposed') OR opened_at > ?)",
                        (project, b["fingerprint"], time.time() - cfg["cooldown_min"] * 60))
        if recent:
            continue  # already open, or in cooldown
        odds = _odds(project, b, cfg)
        if odds:
            b["jev"] = odds
        if odds and odds["real"] < cfg["jev_hold_below"]:
            actions.append((b, "hold"))
            continue
        if cfg["mode"] == "auto" or (cfg["mode"] == "auto_low" and b["severity"] != "high"):
            sure = odds is None or odds["real"] >= cfg["jev_open_min"]
            action = "open" if sure and opened_last_hour < cfg["max_auto_per_hour"] else "propose"
        else:
            action = "propose"
        opened_last_hour += action == "open"
        actions.append((b, action))
    return actions


def _odds(project: str, breach: dict, cfg: dict) -> dict | None:
    if not cfg.get("jev"):
        return None
    from . import jev
    cache = _jev_seen.setdefault(project, {})
    hit = cache.get(breach["fingerprint"])
    if hit and time.time() - hit[0] < cfg["jev_recheck_min"] * 60:
        return hit[1]
    odds = jev.incident_odds(project, breach)
    cache[breach["fingerprint"]] = (time.time(), odds)
    return odds


# ---------------- link a query to code, changes and people ----------------

def query_context(project: str, query: str) -> dict:
    """Tables and filter columns from the SQL -> code that uses them -> PRs that touched that code -> people. Plus index hints."""
    tables, cols = [], []
    try:
        tree = sqlglot.parse_one(query.replace("$", ":p"), read="postgres")
        tables = sorted({t.name for t in tree.find_all(exp.Table)})
        where = tree.find(exp.Where)
        cols = sorted({c.name for c in (where.find_all(exp.Column) if where else [])})
    except Exception:
        tables = re.findall(r'(?:from|join)\s+"?(\w+)"?', query, re.I)
    code, prs = [], {}
    for t in tables:
        for f in graph.neighbors(project, f"table:{t}", {"has_field"}, "out") + graph.neighbors(project, f"table:{t}s", {"has_field"}, "out"):
            if f["id"].split(".")[-1] in cols or not cols:
                for s in graph.neighbors(project, f["id"], {"uses_field", "writes_field"}, "in"):
                    code.append(s["id"])
    for s in dict.fromkeys(code):
        fid = "file:" + s[4:].split("#")[0]
        for pr in graph.neighbors(project, fid, {"touches"}, "in"):
            prs[pr["id"]] = prs.get(pr["id"], 0) + 1
    hints = []
    if cols:
        a = staging.adapter(project, "monitor_db")
        for t in tables:
            try:
                idx = a.indexes(t)
            except Exception:
                continue
            for c in cols:
                if not any(i and i[0].lower() == c.lower() for i in idx):  # only a leading column helps a filter
                    hints.append(f"No index on {t}.{c}, which this query filters on")
    return {"tables": tables, "filter_columns": cols, "code": list(dict.fromkeys(code))[:6],
            "prs": sorted(prs, key=lambda p: -prs[p])[:3], "hints": hints[:3]}


def _qsafe(project, sql):
    try:
        return _q(project, sql)
    except Exception:
        return []


# ---------------- incidents ----------------

def open_incident(project: str, title: str, severity: str, trigger: str, opened_by: str, rule: str = "", fingerprint: str = "",
                  evidence: dict | None = None, status: str = "open") -> str:
    from .pipeline import name
    iid = db.uid("inc")
    db.x("""INSERT INTO incidents(id, project, title, severity, status, trigger, rule, fingerprint, opened_by, opened_at, evidence, relay)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
         (iid, project, title, severity, status, trigger, rule, fingerprint, opened_by, time.time(), json.dumps(evidence or {}, default=str), "[]"))
    incident_relay(iid, "detected", "Monitoring agent" if trigger == "agent" else name(opened_by), trigger if trigger == "agent" else "human",
                   title if status == "open" else f"Proposed: {title}. Waiting for a person to open the war room.")
    db.event(project, None, opened_by, "incident_" + ("opened" if status == "open" else "proposed"), {"incident": iid, "title": title})
    if status == "open":
        threading.Thread(target=diagnose, args=(project, iid), daemon=True).start()
    return iid


INC_STAGES = ["detected", "diagnosed", "owner", "mitigating", "resolved"]


def incident_relay(iid: str, step: str, actor: str, kind: str, note: str):
    r = db.one("SELECT relay FROM incidents WHERE id=?", (iid,))
    items = [i for i in json.loads(r["relay"] or "[]") if i["step"] != step]
    items.append({"step": step, "actor": actor, "kind": kind, "note": note, "at": time.time()})
    items.sort(key=lambda i: INC_STAGES.index(i["step"]))
    db.x("UPDATE incidents SET relay=? WHERE id=?", (json.dumps(items), iid))


def diagnose(project: str, iid: str):
    """What the agents do when a war room opens: link to code and changes, propose an owner and mitigations."""
    from .pipeline import HOURS_CAP, load, name
    inc = db.one("SELECT * FROM incidents WHERE id=?", (iid,))
    ev = json.loads(inc["evidence"] or "{}")
    from . import sources
    if ev.get("frames"):
        ctx = sources.frames_to_code(project, ev["frames"])
    elif ev.get("query") and staging.connected(project, "monitor_db"):
        ctx = query_context(project, ev["query"])
    else:
        ctx = {"code": [], "prs": [], "hints": [], "tables": []}
    # Jev ranks the linked changes by how likely each one caused this, so the owner and rollback target follow the likeliest.
    if ctx.get("prs"):
        from . import jev
        problem = f"{inc['title']}. " + (f"Query: {ev['query'][:500]}" if ev.get("query") else "") + \
                  (f"Error: {ev.get('template', '')[:300]}. Sample: {str(ev.get('sample', ''))[:500]}" if ev.get("template") else "")
        odds = jev.rank_causes(project, "incident", iid, problem, ctx["prs"])
        if odds:
            ctx["prs"] = [pid for pid, _ in odds[0]]
            ev["jev_causes"] = {"candidates": [{"id": pid, "label": (graph.node(project, pid) or {"label": pid})["label"], "p": p} for pid, p in odds[0]],
                                "none": odds[1]}
    ev["context"] = ctx
    from . import tracks  # the team that owns this code hears about it
    files = [c[4:].split("#")[0].split(":", 1)[-1] if c.startswith("sym:") else c.split(":", 2)[-1] for c in ctx.get("code", [])]
    owner_track = tracks.route(project, files)
    if owner_track:
        db.x("UPDATE incidents SET track=? WHERE id=?", (owner_track["id"], iid))
    parts = []
    if ctx["tables"]:
        parts.append("tables " + ", ".join(ctx["tables"]))
    if ctx["code"]:
        parts.append("used by " + ", ".join(c.split("#")[-1] for c in ctx["code"][:3]))
    if ev.get("jev_causes"):
        parts.append("most likely cause " + ", ".join(f"{c['label'].split(' ')[0]} ({round(c['p'] * 100)}%)" for c in ev["jev_causes"]["candidates"][:2]))
    elif ctx["prs"]:
        parts.append("last changed in " + ", ".join((graph.node(project, p) or {"label": p})["label"].split(" ")[0] for p in ctx["prs"]))
    parts += ctx["hints"]
    if ev.get("frames") and not ctx["code"]:
        parts.append("stack frames don't match files in the connected repo")
    if owner_track:
        parts.append(f"{owner_track['name']} owns this area")
    incident_relay(iid, "diagnosed", "Root cause agent", "agent", "; ".join(parts) or "No code linked yet. Needs a person who knows this area.")
    # owner: people who wrote or reviewed the linked PRs, with capacity
    busy = load(project)
    owner, why = None, ""
    for pr in ctx["prs"]:
        for n in graph.neighbors(project, pr, {"authored", "reviewed"}, "in"):
            u = db.one("SELECT id, name, hours_today, shift FROM users WHERE github=?", (n["id"].split(":", 1)[1],))
            if u and db.role_of(u["id"], project) in ("engineer", "lead", "owner") and u["shift"] == "on" and HOURS_CAP - u["hours_today"] - busy.get(u["id"], 0) > 1:
                owner, why = u["id"], f"{'wrote' if n['edge'] == 'authored' else 'reviewed'} {(graph.node(project, pr) or {'label': pr})['label'].split(' ')[0]}"
                break
        if owner:
            break
    if not owner:
        lead = db.one("SELECT user FROM members WHERE project=? AND role IN ('lead','owner') ORDER BY role='lead' DESC", (project,))
        owner, why = (lead["user"], "on call as lead") if lead else (None, "")
    proposals = []
    for h in ctx["hints"]:
        m = re.match(r"No index on (\w+)\.(\w+)", h)
        if m:
            proposals.append({"action": "add_index", "detail": f"CREATE INDEX CONCURRENTLY ON {m.group(1)} ({m.group(2)});", "risk": "low"})
    if ev.get("pid"):
        proposals.append({"action": "cancel_query", "detail": f"SELECT pg_cancel_backend({ev['pid']});", "risk": "medium"})
    if ctx["prs"]:
        proposals.append({"action": "rollback", "detail": f"Revert {(graph.node(project, ctx['prs'][0]) or {'label': ''})['label'].split(' ')[0]} or its deploy", "risk": "high"})
    ev["proposals"] = proposals
    db.x("UPDATE incidents SET evidence=?, owner=?, proposal=? WHERE id=?", (json.dumps(ev, default=str), owner, why, iid))
    incident_relay(iid, "owner", "Dispatcher", "agent", f"{name(owner)} ({why})" if owner else "Nobody linked. A lead should take it.")


def tick(project: str) -> dict:
    from . import sources
    snap = snapshot(project) if staging.connected(project, "monitor_db") else {"statements": {}, "active": [], "at": time.time()}
    breaches = evaluate(project, snap) if staging.connected(project, "monitor_db") else []
    if db.one("SELECT 1 FROM sources WHERE project=? AND enabled=1", (project,)):
        breaches += evaluate_logs(project, sources.poll(project))
    from . import watch
    breaches += watch.breaches(project)  # releases inside their watch window
    _prev.setdefault(project, snap)
    _prev[project]["at"] = time.time()
    acted = []
    for b, action in guard(project, breaches):
        if action == "hold":
            p = b["jev"]["real"]
            db.event(project, None, "monitor", "signal_held", {"title": b["title"], "note": f"{round(p * 100)}% likely real, below the hold line"})
            acted.append({"action": "hold", "rule": b["rule"], "real": p})
            if b.get("release"):
                watch.record(b["release"], b, None, "held")
            continue
        ev = {k: v for k, v in b.items() if k not in ("title",)}
        iid = open_incident(project, b["title"], b["severity"], "agent", "monitor", b["rule"], b["fingerprint"], ev,
                            status="open" if action == "open" else "proposed")
        if b.get("release"):
            watch.record(b["release"], b, iid, action)
        acted.append({"incident": iid, "action": action, "rule": b["rule"]})
    return {"statements": len(snap["statements"]), "active": len(snap["active"]), "breaches": len(breaches), "acted": acted,
            "note": snap.get("statements_error") or snap.get("unsupported")}


def _loop():
    while True:
        for p in db.q("SELECT project FROM secrets WHERE kind='monitor_db' UNION SELECT project FROM sources WHERE enabled=1"):
            cfg = settings(p["project"])
            last = _prev.get(p["project"], {}).get("at", 0)
            if cfg["enabled"] and time.time() - last >= cfg["interval_s"]:
                try:
                    tick(p["project"])
                except Exception as e:
                    print(f"[monitor] {p['project']}: {e}", flush=True)
        time.sleep(5)


def start():
    threading.Thread(target=_loop, daemon=True).start()
