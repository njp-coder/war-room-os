"""API explorer: run the app on staging and record what really happens, instead of inferring it from code.

For each endpoint the sync found:
  1. fill its path and required query parameters with real values from the staging database
     ({order_id} -> an id from orders, ?phone= -> a phone from customer)
  2. call it on the staging URL, recording status, time and the shape of the response
  3. diff pg_stat_statements on the staging database around the call to see which SQL actually ran and on which tables
Results become observed facts: graph edges endpoint -> table marked observed, a context card per endpoint, and a comparison
with what the code map inferred (tables the code seemed to touch that staging never did, and the reverse).

Safety: staging only. GET requests only, unless a lead turns on writes for the project; writes run only against staging.
One request at a time with a timeout. Other traffic on staging during a run can add noise to the SQL diff, and the report says so.
"""
from __future__ import annotations

import ast
import json
import re
import threading
import time

import httpx
import sqlglot
from sqlglot import exp

from . import db, graph, staging

db.x("""CREATE TABLE IF NOT EXISTS explorer_runs(id TEXT PRIMARY KEY, project TEXT, started REAL, ended REAL, status TEXT,
        writes INT, results TEXT DEFAULT '[]', summary TEXT)""")
TIMEOUT_S = 10
PARAM = re.compile(r"\{(\w+)(?::[^}]*)?\}|<(?:\w+:)?(\w+)>|/:(\w+)")
_running: set[str] = set()


# ---------------- inputs ----------------

def _handler_params(project: str, handler_id: str) -> list[dict]:
    """Required simple parameters of a Python handler (not Depends, not a request body)."""
    from .experts import _repo_root
    n = graph.node(project, handler_id)
    root = _repo_root(project)
    if not n or not root:
        return []
    p = root / n["props"].get("file", "")
    if not p.exists() or not p.suffix == ".py":
        return []
    try:
        tree = ast.parse(p.read_text(errors="ignore"))
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno == n["props"].get("line"):
            args = node.args.args
            defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
            out = []
            for a, d in zip(args, defaults):
                ann = ast.unparse(a.annotation) if a.annotation else ""
                if d is not None or ann not in ("str", "int", "float", "bool"):
                    continue  # optional, Depends(...), a body model or a session
                out.append({"name": a.arg, "type": ann})
            return out
    return []


def _value_for(project: str, name: str, schema: dict, cache: dict) -> str | None:
    """A real value for a parameter, from the staging database: order_id -> orders.id, phone -> customer.phone."""
    if name in cache:
        return cache[name]
    a = staging.adapter(project)
    base = re.sub(r"_?id$", "", name).lower()
    candidates = []
    for t, info in schema.items():
        cols = {c.lower(): c for c in info["columns"]}
        tl = t.lower().rstrip("s")
        if base and (tl == base or tl == base.rstrip("s")) and (info["pk"] or ["id"])[0].lower() in cols:
            candidates.append((0, t, (info["pk"] or ["id"])[0]))
        elif name.lower() in cols:
            candidates.append((1, t, cols[name.lower()]))
    for _, t, col in sorted(candidates):
        try:
            q = a.sample(t, [col], f'"{col}" IS NOT NULL' if a.family == "sql" else json.dumps({col: {"$ne": None}}), 1) \
                if a.kind != "dynamodb" else f'SELECT "{col}" FROM "{t}"'
            rows = a.run(q)["rows"]
            if rows and rows[0][0] is not None:
                cache[name] = str(rows[0][0])
                return cache[name]
        except Exception:
            continue
    cache[name] = None
    return None


# ---------------- SQL observed around a call ----------------

def _stmt_counts(project: str) -> dict[str, tuple[int, str]]:
    try:
        a = staging.adapter(project)
        if a.kind != "postgres":
            return {}
        r = a.run("""SELECT queryid::text AS id, calls, query FROM pg_stat_statements
                     WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())""", trusted=True)
        return {row[0]: (int(row[1]), row[2]) for row in r["rows"]}
    except Exception:
        return {}


def _tables(sql: str) -> tuple[set[str], set[str]]:
    reads, writes = set(), set()
    try:
        tree = sqlglot.parse_one(re.sub(r"\$\d+", ":p", sql), read="postgres")
    except Exception:
        return reads, writes
    target = None
    if isinstance(tree, (exp.Insert, exp.Update, exp.Delete)):
        t = tree.find(exp.Table)
        target = t.name if t else None
        if target:
            writes.add(target)
    for t in tree.find_all(exp.Table):
        if t.name and t.name != target and not t.name.startswith("pg_"):
            reads.add(t.name)
    return reads, writes


# ---------------- run ----------------

def start(project: str, by: str) -> str:
    if project in _running:
        raise RuntimeError("An exploration is already running for this project")
    p = db.one("SELECT staging_url, settings FROM projects WHERE id=?", (project,))
    if not p or not p["staging_url"]:
        raise ValueError("Set the staging URL in Setup first")
    writes = bool(json.loads(p["settings"] or "{}").get("explorer_writes"))
    rid = db.uid("xr")
    db.x("INSERT INTO explorer_runs(id, project, started, status, writes) VALUES(?,?,?,?,?)", (rid, project, time.time(), "running", int(writes)))
    db.event(project, None, by, "explorer_started", {"note": "reads only" if not writes else "reads and writes, on staging"})
    _running.add(project)
    threading.Thread(target=_run, args=(project, rid, p["staging_url"].rstrip("/"), writes), daemon=True).start()
    return rid


def _run(project: str, rid: str, base: str, writes: bool):
    results = []
    try:
        try:
            httpx.get(base + "/", timeout=5)
        except Exception as e:
            raise RuntimeError(f"Staging isn't reachable at {base}: {type(e).__name__}") from e
        schema = staging.introspect(project, refresh=True) if staging.connected(project) else {}
        sql_on = staging.connected(project) and bool(_stmt_counts(project))
        cache: dict = {}
        eps = db.q("SELECT id, label FROM nodes WHERE project=? AND type='endpoint' ORDER BY label", (project,))
        for e in eps:
            method, _, path = e["label"].partition(" ")
            r = {"endpoint": e["id"], "label": e["label"], "method": method}
            if method not in ("GET", "HEAD") and not writes:
                r.update(status="skipped", note="writes are off for this project")
                results.append(r)
                continue
            handlers = [h["id"] for h in graph.neighbors(project, e["id"], {"handled_by"}, "out")]
            url, missing = path, []
            for m in PARAM.finditer(path):  # FastAPI {id}, Flask <int:id>, Express /:id
                name = m.group(1) or m.group(2) or m.group(3)
                v = _value_for(project, name, schema, cache) if schema else None
                if v is None:
                    missing.append(name)
                url = url.replace(m.group(0), ("/" if m.group(3) else "") + (v or ""), 1)
            params = {}
            for prm in (_handler_params(project, handlers[0]) if handlers else []):
                if prm["name"] in url or f"{{{prm['name']}}}" in path:
                    continue
                v = _value_for(project, prm["name"], schema, cache) if schema else None
                if v is None:
                    missing.append(prm["name"])
                else:
                    params[prm["name"]] = v
            if missing:
                r.update(status="skipped", note=f"no real value found for {', '.join(missing)}")
                results.append(r)
                continue
            before = _stmt_counts(project) if sql_on else {}
            t0 = time.perf_counter()
            try:
                resp = httpx.request(method, base + url, params=params, timeout=TIMEOUT_S)
                r.update(status=resp.status_code, ms=round((time.perf_counter() - t0) * 1000, 1), url=url + (("?" + "&".join(f"{k}=…" for k in params)) if params else ""))
                try:
                    body = resp.json()
                    r["shape"] = sorted(body.keys())[:12] if isinstance(body, dict) else (f"list of {len(body)}" if isinstance(body, list) else type(body).__name__)
                except Exception:
                    r["shape"] = resp.headers.get("content-type", "")
            except Exception as ex:
                r.update(status="error", note=f"{type(ex).__name__}: {str(ex)[:120]}")
            if sql_on:
                after = _stmt_counts(project)
                reads, writes_t, sqls = set(), set(), []
                for qid, (calls, q) in after.items():
                    if calls > before.get(qid, (0, ""))[0] and "pg_stat_statements" not in q:
                        rd, wr = _tables(q)
                        reads |= rd
                        writes_t |= wr
                        sqls.append(q[:300])
                r.update(reads=sorted(reads), writes=sorted(writes_t), queries=len(sqls), sql=sqls[:4])
            results.append(r)
            db.x("UPDATE explorer_runs SET results=? WHERE id=?", (json.dumps(results), rid))
        _record(project, results)
        ok = sum(1 for x in results if isinstance(x.get("status"), int) and x["status"] < 400)
        failed = [x for x in results if isinstance(x.get("status"), int) and x["status"] >= 500]
        summary = (f"Called {sum(1 for x in results if x.get('ms') is not None)} of {len(results)} endpoints on staging: {ok} answered, "
                   f"{len(failed)} failed with a server error." + ("" if sql_on else " SQL wasn't traced: connect the staging database with pg_stat_statements readable."))
        db.x("UPDATE explorer_runs SET status='done', ended=?, results=?, summary=? WHERE id=?", (time.time(), json.dumps(results), summary, rid))
        db.event(project, None, "explorer", "explorer_done", {"note": summary})
    except Exception as e:
        db.x("UPDATE explorer_runs SET status='error', ended=?, results=?, summary=? WHERE id=?",
             (time.time(), json.dumps(results), str(e)[:300], rid))
    finally:
        _running.discard(project)


def _record(project: str, results: list[dict]):
    """Observed facts into the graph and memory, replacing the previous run's."""
    from .context import save_cards
    db.x("DELETE FROM edges WHERE project=? AND source='explorer'", (project,))
    edges, cards = [], []
    for r in results:
        if r.get("ms") is None:
            continue
        for t in r.get("reads", []):
            edges.append((r["endpoint"], f"table:{t}", "observed_reads", "explorer"))
        for t in r.get("writes", []):
            edges.append((r["endpoint"], f"table:{t}", "observed_writes", "explorer"))
        text = (f"Observed on staging: {r['label']} returned {r['status']} in {r['ms']} ms"
                + (f", reading {', '.join(r['reads'])}" if r.get("reads") else "") + (f", writing {', '.join(r['writes'])}" if r.get("writes") else "")
                + (f", {r['queries']} queries" if r.get("queries") else "") + ".")
        cards.append({"id": f"observed:{r['endpoint']}", "text": text, "metadata": {"type": "observed", "endpoint": r["label"], "status": str(r["status"])}})
    if edges:
        graph.add_edges(project, edges)
    save_cards(project, "knowledge", cards)


def latest(project: str) -> dict | None:
    r = db.one("SELECT * FROM explorer_runs WHERE project=? ORDER BY started DESC", (project,))
    if not r:
        return None
    r["results"] = json.loads(r["results"] or "[]")
    r["running"] = project in _running
    # code map vs reality, per endpoint
    for x in r["results"]:
        if x.get("reads") is None:
            continue
        inferred = set()
        for h in graph.neighbors(project, x["endpoint"], {"handled_by"}, "out"):
            frontier, seen = [h["id"]], {h["id"]}
            for _ in range(2):
                frontier = [c["id"] for f in frontier for c in graph.neighbors(project, f, {"calls"}, "out") if c["id"] not in seen]
                seen |= set(frontier)
            for s in seen:
                inferred |= {f["id"].split(":", 1)[1].split(".")[0] for f in graph.neighbors(project, s, {"uses_field", "writes_field"}, "out")}
        observed = set(x.get("reads", [])) | set(x.get("writes", []))
        x["only_observed"] = sorted(observed - inferred)
        x["only_inferred"] = sorted(inferred - observed)
    return r
