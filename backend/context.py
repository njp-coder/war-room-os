"""Project context pipeline: GitHub -> cards (Moss) + graph (SQLite).

Layers: structure, data, service, history, people, docs. All parsing, no LLM.
Incremental: files whose blob SHA is unchanged are not re-parsed.
"""
from __future__ import annotations

import json
import re
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path

from . import db, github, graph, parsers
from .memory import memory_for

LAYERS = ["clone", "structure", "data", "service", "history", "people", "docs"]


def _progress(project: str, repo: str, layer: str, state: str, detail: str = ""):
    row = db.one("SELECT progress FROM repos WHERE project=? AND full_name=?", (project, repo))
    prog = json.loads(row["progress"] or "{}") if row else {}
    prog[layer] = {"state": state, "detail": detail, "at": time.time()}
    status = "error" if state == "error" else ("ready" if layer == "docs" and state == "done" else "syncing")
    db.x("UPDATE repos SET progress=?, status=? WHERE project=? AND full_name=?", (json.dumps(prog), status, project, repo))


def save_cards(project: str, index: str, cards: list[dict]):
    if not cards:
        return
    memory_for(project).add(index, cards)
    db.xmany("INSERT OR REPLACE INTO cards VALUES(?,?,?,?,?)",
             [(project, c["id"], index, c["text"], json.dumps(c["metadata"])) for c in cards])


def _mount_prefixes(parsed: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Follow include_router / register_blueprint chains across files: file -> full mount prefix.
    Prefixes that come from settings (not literals) are reported per file instead of guessed."""
    by_module = {p[:-3].replace("/", ".").replace(".__init__", ""): p for p in parsed if p.endswith(".py")}

    def file_of(module: str) -> str | None:
        hits = [f for m, f in by_module.items() if m == module or m.endswith("." + module)]
        return hits[0] if len(hits) == 1 else None

    consts: dict[str, list[str]] = {}
    for res in parsed.values():
        for k, v in res.get("constants", {}).items():
            consts.setdefault(k, []).append(v)
    edges = []  # (parent_file, child_file, prefix, unresolved)
    for path, res in parsed.items():
        names = res.get("import_names", {})
        for m in res.get("mounts", []):
            child = m["child"].split(".")[0]  # orders.router -> orders ; api_router -> api_router
            module = names.get(child)
            child_file = file_of(module) if module else None
            if not child_file and child in names:  # `from app.api.routes import items` handled above; `from x import router as r`
                child_file = file_of(names[child].rsplit(".", 1)[0])
            if child_file:
                p, u = m["prefix"], m["unresolved"]
                if u:  # settings.API_V1_STR -> the one string constant with that name, if it's unambiguous
                    vals = set(consts.get(u.split(".")[-1], []))
                    if len(vals) == 1:
                        p, u = vals.pop(), None
                edges.append((path, child_file, p, u))
    prefix: dict[str, str] = {}
    unresolved: dict[str, str] = {}

    def resolve(f: str, seen: tuple = ()) -> str:
        if f in prefix:
            return prefix[f]
        parents = [e for e in edges if e[1] == f and e[0] not in seen]
        if not parents:
            return ""
        parent, _, p, u = parents[0]
        up = resolve(parent, seen + (f,))
        if u or parent in unresolved:
            unresolved[f] = u or unresolved[parent]
        prefix[f] = parsers._join(up, p) if (up or p) else ""
        return prefix[f]

    for _, child, _, _ in edges:
        resolve(child)
    return {f: p for f, p in prefix.items() if p}, unresolved


def load_cards(project: str):
    """Warm a project's memory at startup. The local mirror fills now; Moss only gets what changed since its last sync."""
    import hashlib
    db.x("CREATE TABLE IF NOT EXISTS moss_sync(project TEXT, idx TEXT, fingerprint TEXT, at REAL, PRIMARY KEY(project, idx))")
    by_index = defaultdict(list)
    for r in db.q("SELECT id, idx, text, meta FROM cards WHERE project=? ORDER BY id", (project,)):
        by_index[r["idx"]].append({"id": r["id"], "text": r["text"], "metadata": json.loads(r["meta"])})
    mem = memory_for(project)
    synced = {r["idx"]: r["fingerprint"] for r in db.q("SELECT idx, fingerprint FROM moss_sync WHERE project=?", (project,))}

    def mark(idx: str, fp: str):
        db.x("INSERT OR REPLACE INTO moss_sync VALUES(?,?,?,?)", (project, idx, fp, time.time()))

    for idx, cards in by_index.items():
        fp = hashlib.sha1("".join(c["id"] + c["text"] for c in cards).encode()).hexdigest()
        mem.warm(idx, cards, fp, synced.get(idx), mark)


def ingest_repo(project: str, full_name: str, pr_limit: int = 60):
    try:
        _ingest(project, full_name, pr_limit)
    except Exception as e:
        traceback.print_exc()
        _progress(project, full_name, "error", "error", str(e)[:300])
        db.x("UPDATE repos SET status='error' WHERE project=? AND full_name=?", (project, full_name))


def _ingest(project: str, repo: str, pr_limit: int):
    _progress(project, repo, "clone", "running", "Shallow clone")
    root = github.clone(project, repo)
    shas = github.blob_shas(root)
    known = {r["path"]: r["sha"] for r in db.q("SELECT path, sha FROM blobs WHERE project=? AND repo=?", (project, repo))}
    changed = {p for p, s in shas.items() if known.get(p) != s}
    _progress(project, repo, "clone", "done", f"{len(shas)} files, {len(changed)} new or changed")

    # ---------- structure ----------
    _progress(project, repo, "structure", "running")
    files = [p for p in parsers.walk(root)]
    rel = {str(p.relative_to(root)): p for p in files}
    parsed: dict[str, dict] = {}
    for path, p in rel.items():
        lang = parsers.CODE_EXT.get(p.suffix)
        if lang not in ("python", "ts", "js"):
            continue
        src = parsers.scrub(p.read_text(errors="ignore"))
        parsed[path] = parsers.parse_python(path, src) if lang == "python" else parsers.parse_js(path, src)

    nodes, edges, kcards = [], [], []
    for path in rel:
        fid = f"file:{repo}:{path}"
        nodes.append((fid, "file", path, {"repo": repo, "test": parsers.is_test(path), "sha": shas.get(path)}))
    sym_by_name = defaultdict(list)
    for path, res in parsed.items():
        fid = f"file:{repo}:{path}"
        if path in changed:
            graph.close_edges_from(project, fid)
        for s in res["symbols"]:
            sid = f"sym:{repo}:{path}#{s['name']}"
            sym_by_name[s["name"].split(".")[-1]].append(sid)
            nodes.append((sid, "symbol", s["name"], {"file": path, "line": s["line"], "end": s["end"], "kind": s["kind"]}))
            edges.append((fid, sid, "defines", shas.get(path, "")))
            kcards.append({"id": sid, "text": f"{path} {s['kind']} {s['name']}: {s['sig']} {s['doc']}".strip(),
                           "metadata": {"type": "symbol", "file": path, "repo": repo, "line": s["line"],
                                        "test": str(parsers.is_test(path)).lower()}})
    # calls (resolved by name, ambiguous names skipped) and imports (resolved to files)
    module_index = _module_index(rel)
    for path, res in parsed.items():
        for s in res["symbols"]:
            sid = f"sym:{repo}:{path}#{s['name']}"
            for c in s["calls"]:
                cands = sym_by_name.get(c, [])
                same_file = [x for x in cands if f":{path}#" in x]
                target = same_file[:1] or (cands if len(cands) == 1 else [])
                for t in target:
                    if t != sid:
                        edges.append((sid, t, "calls", ""))
        for imp in res["imports"]:
            target = _resolve_import(path, imp, module_index)
            if target:
                edges.append((f"file:{repo}:{path}", f"file:{repo}:{target}", "imports", ""))
    graph.add_nodes(project, nodes)
    graph.add_edges(project, edges)
    save_cards(project, "knowledge", kcards)
    _progress(project, repo, "structure", "done", f"{len(parsed)} code files, {len(kcards)} symbols, "
              f"{sum(1 for e in edges if e[2] == 'calls')} calls, {sum(1 for e in edges if e[2] == 'imports')} imports")

    # ---------- data (schemas, migrations, field usage) ----------
    _progress(project, repo, "data", "running")
    tables: dict[str, dict] = {}
    alters = []
    for path, p in rel.items():
        low = path.lower()
        if p.suffix == ".sql" or (p.suffix in (".py", ".ts", ".js") and "migration" in low):
            src = p.read_text(errors="ignore")
            res = parsers.parse_sql(src) if p.suffix == ".sql" else {"tables": [], "alters": [
                {"table": m.group(1), "sql": m.group(0)[:300]} for m in re.finditer(
                    r"(?:alter_column|add_column|drop_column|alterTable|addColumn|renameColumn)\(\s*['\"](\w+)['\"][^)]*\)", src)]}
            for t in res["tables"]:
                tables.setdefault(t["table"], {"fields": {}, "source": path})["fields"].update({f["name"]: f for f in t["fields"]})
            for a in res.get("alters", []):
                alters.append({**a, "file": path})
        elif p.name == "schema.prisma":
            res = parsers.parse_prisma(p.read_text(errors="ignore"))
            for t in res["tables"]:
                tables.setdefault(t["table"], {"fields": {}, "source": path, "model": t.get("model")})["fields"].update({f["name"]: f for f in t["fields"]})
    for path, res in parsed.items():
        for m in res["models"]:
            tables.setdefault(m["table"], {"fields": {}, "source": path, "model": m["model"]})["fields"].update({f["name"]: f for f in m["fields"]})

    dnodes, dedges, dcards = [], [], []
    for tname, t in tables.items():
        tid = f"table:{tname}"
        dnodes.append((tid, "table", tname, {"source": t["source"], "model": t.get("model")}))
        for fname, f in t["fields"].items():
            fid = f"field:{tname}.{fname}"
            dnodes.append((fid, "field", f"{tname}.{fname}", {"type": f.get("type"), "nullable": f.get("nullable"), "pk": f.get("pk"), "fk": f.get("fk")}))
            dedges.append((tid, fid, "has_field", t["source"]))
            if f.get("fk"):
                dedges.append((fid, f"field:{f['fk']}", "references", t["source"]))
                dedges.append((tid, f"table:{f['fk'].split('.')[0]}", "references", t["source"]))
            dcards.append({"id": fid, "text": f"Field {tname}.{fname} {f.get('type', '')} {'nullable' if f.get('nullable') else 'required'} (from {t['source']})",
                           "metadata": {"type": "field", "fields": f"{tname}.{fname}", "table": tname}})
    for a in alters:
        t = tables.get(a["table"], {"fields": {}})
        for fname in t["fields"]:
            if re.search(rf"\b{re.escape(fname)}\b", a["sql"]):
                dedges.append((f"file:{repo}:{a['file']}", f"field:{a['table']}.{fname}", "alters", a["sql"][:120]))
    # field usage: a symbol that names the column and its table or model (heuristic, labelled as such).
    # Only code in the same language family as the table's definition: Python models are used by Python code,
    # Prisma/TS models by TS/JS code. UI components that merely mention "email" don't count.
    fam = lambda p: "py" if p.endswith(".py") else ("js" if re.search(r"\.(tsx?|jsx?|mjs|prisma)$", p) else "sql")  # noqa: E731
    for path, res in parsed.items():
        if (re.search(r"(^|/)(components|pages|app|routes|views|hooks|client)/", path) and not path.endswith(".py")) or parsers.is_test(path):
            continue
        for s in res["symbols"]:
            body = s["body"]
            for tname, t in tables.items():
                if fam(t["source"]) not in (fam(path), "sql"):
                    continue
                anchors = [tname, t.get("model") or "", tname.rstrip("s")]
                if not any(a and re.search(rf"\b{re.escape(a)}\b", body, re.I) for a in anchors):
                    continue
                for fname in t["fields"]:
                    if len(fname) < 3 or fname in ("id",):
                        continue
                    if re.search(rf"\b{re.escape(fname)}\b", body):
                        writes = re.search(rf"(\.{re.escape(fname)}\s*=[^=]|\b{re.escape(fname)}\s*[:=]\s*[^=]|SET\s+{re.escape(fname)}\b)", body)
                        dedges.append((f"sym:{repo}:{path}#{s['name']}", f"field:{tname}.{fname}",
                                       "writes_field" if writes else "uses_field", "heuristic"))
    graph.add_nodes(project, dnodes)
    graph.add_edges(project, dedges)
    save_cards(project, "knowledge", dcards)
    _progress(project, repo, "data", "done", f"{len(tables)} tables, {sum(len(t['fields']) for t in tables.values())} fields, "
              f"{len(alters)} migration changes, {sum(1 for e in dedges if e[2] in ('uses_field', 'writes_field'))} code-to-field links")

    # ---------- service (endpoints) ----------
    _progress(project, repo, "service", "running")
    snodes, sedges, scards = [], [], []
    mount_prefix, unresolved = _mount_prefixes(parsed)
    for path, res in parsed.items():
        for r in res["routes"]:
            r["path"] = parsers._join(mount_prefix.get(path, ""), r["path"])
            if path in unresolved:
                r["unresolved_prefix"] = unresolved[path]
            rid = f"route:{r['method']} {r['path']}@{path}"
            snodes.append((rid, "endpoint", f"{r['method']} {r['path']}", {"file": path, **({"unresolved_prefix": r["unresolved_prefix"]} if r.get("unresolved_prefix") else {})}))
            if r["handler"]:
                sedges.append((rid, f"sym:{repo}:{path}#{r['handler']}", "handled_by", ""))
            scards.append({"id": rid, "text": f"Endpoint {r['method']} {r['path']} in {path}",
                           "metadata": {"type": "endpoint", "file": path, "method": r["method"], "path": r["path"]}})
    graph.add_nodes(project, snodes)
    # every file is parsed on each sync, so this is the full set: endpoints that no longer exist (renamed paths,
    # deleted routes, a prefix the parser now resolves) are removed with their edges instead of lingering as duplicates
    live = {n[0] for n in snodes}
    stale = [r["id"] for r in db.q("SELECT id, props FROM nodes WHERE project=? AND type='endpoint'", (project,))
             if r["id"] not in live and json.loads(r["props"] or "{}").get("file") in parsed]
    for sid in stale:
        db.x("DELETE FROM nodes WHERE project=? AND id=?", (project, sid))
        db.x("DELETE FROM edges WHERE project=? AND (src=? OR dst=?)", (project, sid, sid))
        db.x("DELETE FROM cards WHERE project=? AND id=?", (project, sid))
    graph.add_edges(project, sedges)
    save_cards(project, "knowledge", scards)
    _progress(project, repo, "service", "done", f"{len(snodes)} endpoints")

    # ---------- history ----------
    _progress(project, repo, "history", "running", f"Last {pr_limit} PRs with reviews")
    prs = github.pulls(repo, pr_limit)
    issues = github.issues(repo, 60)
    deps = github.deployments(repo)
    hnodes, hedges, ccards = [], [], []
    for pr in prs:
        pid = f"pr:{repo}#{pr['number']}"
        why = " ".join(f"{who}: {txt}" for who, txt in pr["review_notes"][:6])
        hnodes.append((pid, "pr", f"#{pr['number']} {pr['title']}", {"url": pr["url"], "state": pr["state"],
                       "merged_at": pr["merged_at"], "milestone": pr["milestone"], "labels": pr["labels"], "base": pr["base"], "head": pr["head"]}))
        hnodes.append((f"person:{pr['author']}", "person", pr["author"], {}))
        hedges.append((f"person:{pr['author']}", pid, "authored", pr["url"]))
        for rv in pr["reviewers"]:
            hnodes.append((f"person:{rv}", "person", rv, {}))
            hedges.append((f"person:{rv}", pid, "reviewed", pr["url"]))
        for f in pr["files"]:
            hedges.append((pid, f"file:{repo}:{f['path']}", "touches", pr["url"]))
            hunks = [(int(m.group(1)), int(m.group(1)) + int(m.group(2) or 1)) for m in re.finditer(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", f["patch"])]
            db.x("INSERT OR REPLACE INTO pr_files VALUES(?,?,?,?)", (project, pid, f["path"], json.dumps(hunks)))
        db.x("DELETE FROM reviews WHERE project=? AND pr=?", (project, pid))
        db.xmany("INSERT INTO reviews VALUES(?,?,?,?)", [(project, pid, who, txt) for who, txt in pr["review_notes"] if txt.strip()])
        ccards.append({"id": pid, "text": f"PR #{pr['number']} {pr['title']} by {pr['author']}"
                       f"{', reviewed by ' + ', '.join(pr['reviewers']) if pr['reviewers'] else ''}. "
                       f"{pr['body'][:400]} Files: {', '.join(f['path'] for f in pr['files'][:8])}. Review: {why[:600]}",
                       "metadata": {"type": "pr", "pr": pr["number"], "author": pr["author"], "reviewers": pr["reviewers"],
                                    "files": [f["path"] for f in pr["files"][:40]], "merged_at": pr["merged_at"] or "",
                                    "milestone": pr["milestone"] or "", "labels": pr["labels"], "url": pr["url"], "repo": repo}})
    for i in issues:
        ccards.append({"id": f"issue:{repo}#{i['number']}", "text": f"Issue #{i['number']} {i['title']}. {i['body'][:400]}",
                       "metadata": {"type": "issue", "state": i["state"], "labels": i["labels"], "url": i["url"], "author": i["author"], "title": i["title"], "number": i["number"],
                                    "milestone": i["milestone"] or "", "repo": repo}})
    for d in deps:
        ccards.append({"id": f"deploy:{repo}:{d['id']}", "text": f"Deploy to {d['env']} of {d['ref']} ({d['sha']}) at {d['created_at']}",
                       "metadata": {"type": "deploy", "env": d["env"], "created_at": d["created_at"], "repo": repo}})
    # co-change: files that historically change together (a strong "also look at" signal)
    pairs = Counter()
    for pr in prs:
        code_files = sorted({f["path"] for f in pr["files"] if parsers.CODE_EXT.get(Path(f["path"]).suffix)})
        if 1 < len(code_files) <= 20:
            for i, a in enumerate(code_files):
                for b in code_files[i + 1:]:
                    pairs[(a, b)] += 1
    for (a, b), n in pairs.items():
        if n >= 2:
            hedges += [(f"file:{repo}:{a}", f"file:{repo}:{b}", "co_changes", str(n)),
                       (f"file:{repo}:{b}", f"file:{repo}:{a}", "co_changes", str(n))]
    graph.add_nodes(project, hnodes)
    graph.add_edges(project, hedges)
    save_cards(project, "changes", ccards)
    _progress(project, repo, "history", "done", f"{len(prs)} PRs, {len(issues)} issues, {len(deps)} deploys")

    # ---------- people ----------
    _progress(project, repo, "people", "running")
    owners_src = next((rel[p] for p in ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS") if p in rel), None)
    rules = parsers.parse_codeowners(owners_src.read_text()) if owners_src else []
    pedges, areas, reviewed = [], defaultdict(Counter), defaultdict(Counter)
    for pr in prs:
        for f in pr["files"]:
            top = "/".join(f["path"].split("/")[:2])
            areas[pr["author"]][top] += 1
            for rv in pr["reviewers"]:
                reviewed[rv][top] += 1
    for path in rel:
        for owner in parsers.owners_for(path, rules):
            pedges.append((f"person:{owner}", f"file:{repo}:{path}", "owns", "CODEOWNERS"))
    pcards = []
    for login in set(areas) | set(reviewed) | {o for _, os_ in rules for o in os_}:
        wrote = ", ".join(a for a, _ in areas[login].most_common(6))
        rev = ", ".join(a for a, _ in reviewed[login].most_common(6))
        owns = ", ".join(sorted({pat for pat, os_ in rules if login in os_})[:6])
        pcards.append({"id": f"person:{login}", "text": f"{login}. Wrote: {wrote or 'none'}. Reviewed: {rev or 'none'}. Owns: {owns or 'none'}",
                       "metadata": {"type": "person", "person": login, "repo": repo}})
    graph.add_nodes(project, [(f"person:{l}", "person", l, {}) for l in [c["metadata"]["person"] for c in pcards]])
    graph.add_edges(project, pedges)
    save_cards(project, "people", pcards)
    _progress(project, repo, "people", "done", f"{len(pcards)} people, CODEOWNERS {'found' if rules else 'missing'}")

    # ---------- docs ----------
    _progress(project, repo, "docs", "running")
    dcards = []
    for path, p in rel.items():
        if p.suffix.lower() in parsers.DOC_EXT and not parsers.is_test(path):
            for i, sec in enumerate(parsers.doc_sections(path, parsers.scrub(p.read_text(errors="ignore")))[:40]):
                dcards.append({"id": f"doc:{repo}:{path}#{i}", "text": f"{path} / {sec['title']}: {sec['text'][:700]}",
                               "metadata": {"type": "doc", "file": path, "repo": repo}})
    save_cards(project, "knowledge", dcards)
    db.xmany("INSERT OR REPLACE INTO blobs VALUES(?,?,?,?)", [(project, repo, p, s) for p, s in shas.items()])
    db.x("UPDATE repos SET synced=? WHERE project=? AND full_name=?", (time.time(), project, repo))
    _progress(project, repo, "docs", "done", f"{len(dcards)} doc sections")
    db.event(project, None, "context", "repo_synced", {"repo": repo})
    try:
        from . import experts
        experts.build(project)
    except Exception as e:
        print(f"[experts] build failed: {e}")


def _module_index(rel: dict[str, Path]) -> dict[str, str]:
    idx = {}
    for path in rel:
        if path.endswith(".py"):
            mod = path[:-3].replace("/", ".")
            idx[mod.removesuffix(".__init__")] = path
            parts = mod.split(".")
            for i in range(1, len(parts)):  # also index without top-level src dirs
                idx.setdefault(".".join(parts[i:]).removesuffix(".__init__"), path)
        elif re.search(r"\.(tsx?|jsx?|mjs)$", path):
            idx[re.sub(r"\.(tsx?|jsx?|mjs)$", "", path)] = path
    return idx


def _resolve_import(path: str, imp: str, idx: dict[str, str]) -> str | None:
    if imp.startswith("."):
        base = str(Path(path).parent / imp)
        norm = str(Path(base)).replace("\\", "/")
        parts = []
        for seg in norm.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg not in (".", ""):
                parts.append(seg)
        key = "/".join(parts)
        return idx.get(key) or idx.get(key + "/index")
    if imp.startswith("@/"):
        return idx.get(imp[2:]) or idx.get("src/" + imp[2:])
    return idx.get(imp)


def coverage(project: str) -> dict:
    s = graph.stats(project)
    n, e = s["nodes"], s["edges"]
    gaps = []
    if not n.get("table"):
        gaps.append("No schema found (SQL, Prisma or ORM models). Data lineage and migration risks will be thin.")
    if not db.q("SELECT 1 FROM edges WHERE project=? AND type='owns' LIMIT 1", (project,)):
        gaps.append("CODEOWNERS missing. Ownership comes from PR history only.")
    if not n.get("endpoint"):
        gaps.append("No HTTP routes detected. Blast radius stops at code and data.")
    if not n.get("pr"):
        gaps.append("No pull requests found. The 'why' behind changes will be missing.")
    counts = {r["idx"]: r["n"] for r in db.q("SELECT idx, count(*) n FROM cards WHERE project=? GROUP BY idx", (project,))}
    return {"nodes": n, "edges": e, "cards": counts, "gaps": gaps}
