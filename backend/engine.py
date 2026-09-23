"""Context engine: the smallest context that is sufficient for the correct decision.

Agent-native operations over the project graph + Moss memory:
  understand(task)          current flow, where it lives, owners, recent changes
  find_precedent(task)      "where have we done something like this before", with its anatomy
  conventions()             layer map of the repo, where new code of a kind usually goes
  impact(target)            what a change touches: callers, endpoints, data, tests, co-changes, people
  explain(symbol)           one symbol in context
  context(task, state)      stateful: re-plans as the agent inspects files, edits, and sees test failures

Every item says why it is included. Output is packed to a token budget, most useful first.
Pure retrieval and graph work: zero LLM tokens.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from . import db, graph
from .memory import memory_for

LAYER_NAMES = {
    "routes": "HTTP routes / controllers", "api": "HTTP routes / controllers", "controllers": "HTTP routes / controllers",
    "handlers": "HTTP routes / controllers", "services": "business logic", "service": "business logic", "domain": "business logic",
    "crud": "persistence", "repositories": "persistence", "repository": "persistence", "models": "data models", "db": "persistence",
    "schemas": "request/response schemas", "integrations": "external integrations", "clients": "external integrations",
    "core": "config and core", "config": "config and core", "utils": "shared helpers", "lib": "shared helpers",
    "components": "UI components", "routes_ui": "UI routes", "pages": "UI pages", "hooks": "UI hooks",
    "tests": "tests", "test": "tests", "__tests__": "tests", "migrations": "migrations", "alembic": "migrations",
}


def _tok(chars: int) -> int:
    return max(1, chars // 4)


def _file_of(nid: str) -> str | None:
    if nid.startswith("sym:"):
        return "file:" + nid[4:].split("#")[0]
    if nid.startswith("file:"):
        return nid
    return None


def _label(project: str, nid: str) -> str:
    n = graph.node(project, nid)
    return n["label"] if n else nid.split(":", 1)[-1]


def _path(nid: str) -> str:
    return nid.split(":", 2)[-1].split("#")[0]


def tests_covering(project: str, nid: str) -> list[str]:
    """Test files that import the file, or whose symbols call the symbol (or any symbol in the file)."""
    f = _file_of(nid)
    out = set()
    if f:
        for nb in graph.neighbors(project, f, {"imports"}, "in"):
            if _is_test(nb["id"]):
                out.add(nb["id"])
    targets = [nid] if nid.startswith("sym:") else [n["id"] for n in graph.neighbors(project, f, {"defines"}, "out")] if f else []
    for t in targets[:40]:
        for nb in graph.neighbors(project, t, {"calls"}, "in"):
            if _is_test(nb["id"]):
                out.add(_file_of(nb["id"]) or nb["id"])
    if f:  # naming convention: test_users.py / users.test.ts / users.spec.ts mirror users.py
        stem = re.sub(r"\.(py|tsx?|jsx?)$", "", _path(f).split("/")[-1])
        if len(stem) > 2:
            for r in db.q("SELECT id FROM nodes WHERE project=? AND type='file' AND (label LIKE ? OR label LIKE ? OR label LIKE ?)",
                          (project, f"%test_{stem}.%", f"%{stem}.test.%", f"%{stem}.spec.%")):
                out.add(r["id"])
    return sorted(out)


def _is_test(nid: str) -> bool:
    return bool(re.search(r"(^|/)(tests?|__tests__|spec)/|(_test|\.test|\.spec|test_)", _path(nid)))


def seeds(project: str, task: str, k: int = 8, exclude: set[str] | None = None) -> list:
    mem = memory_for(project)
    hits = [h for h in mem.search("knowledge", task, top_k=k * 2)
            if h.metadata.get("type") in ("symbol", "endpoint", "field", "doc") and h.id not in (exclude or set())]
    return hits[:k]


# ---------------- operations ----------------

def understand(project: str, task: str) -> list[dict]:
    hits = seeds(project, task)
    sections = []
    eps = [h for h in hits if h.id.startswith("route:")]
    flow = []
    for ep in eps[:2]:
        for h in graph.neighbors(project, ep.id, {"handled_by"}, "out"):
            chain = [ep.id, h["id"]]
            cur = h["id"]
            for _ in range(3):
                callees = [c["id"] for c in graph.neighbors(project, cur, {"calls"}, "out") if not _is_test(c["id"])]
                if not callees:
                    break
                cur = callees[0]
                chain.append(cur)
            flow.append({"id": ep.id, "text": " -> ".join(_label(project, c) for c in chain),
                         "why": "request path through the code"})
    if flow:
        sections.append({"title": "Current flow", "items": flow})
    ranked, _ = rank_files(project, task)
    where = []
    for f, _sc in ranked[:5]:
        syms = [h for h in hits if _file_of(h.id) == f]
        where.append({"id": f, "text": _path(f) + (f": {syms[0].text.split(':', 1)[-1].strip()[:90]}" if syms else ""),
                      "why": "matching code" if syms else "similar past changes touched it"})
    sections.append({"title": "Where it lives", "items": where})
    docs = [h for h in hits if h.id.startswith("doc:")][:1]
    if docs:
        sections.append({"title": "Docs", "items": [{"id": d.id, "text": d.text[:160].replace("\n", " "), "why": "related documentation"} for d in docs]})
    fields = set()
    for h in hits:
        for nb in graph.neighbors(project, h.id, {"uses_field", "writes_field"}, "out"):
            fields.add((nb["id"], nb["edge"]))
    if fields:
        sections.append({"title": "Data it touches", "items": [
            {"id": f, "text": f"{_label(project, f)} ({'written' if e == 'writes_field' else 'read'})", "why": "field used by matching code"}
            for f, e in sorted(fields)[:6]]})
    files = {f for f, _ in ranked[:5]}
    prs = Counter()
    for f in files:
        for nb in graph.neighbors(project, f, {"touches"}, "in"):
            prs[nb["id"]] += 1
    if prs:
        sections.append({"title": "Recent changes here", "items": [
            {"id": p, "text": _label(project, p), "why": f"touched {n} of the matching files"} for p, n in prs.most_common(3)]})
    people = Counter()
    for p, _ in prs.most_common(5):
        for nb in graph.neighbors(project, p, {"authored", "reviewed"}, "in"):
            people[nb["id"]] += 1
    for f in files:
        for nb in graph.neighbors(project, f, {"owns"}, "in"):
            people[nb["id"]] += 2
    if people:
        sections.append({"title": "Who knows it", "items": [
            {"id": p, "text": p.split(":", 1)[1], "why": "wrote, reviewed or owns this area"} for p, _ in people.most_common(3)]})
    return sections


def find_precedent(project: str, task: str) -> list[dict]:
    """Rank existing implementations by similarity x completeness (has route, tests, registration)."""
    hits = [h for h in seeds(project, task, k=12) if h.id.startswith("sym:") and not _is_test(h.id)]
    by_file = defaultdict(list)
    for h in hits:
        by_file[_file_of(h.id)].append(h)
    cands = []
    for f, hs in by_file.items():
        matched = {h.id for h in hs}
        pairs = [(ep["id"], s["id"]) for s in graph.neighbors(project, f, {"defines"}, "out")
                 for ep in graph.neighbors(project, s["id"], {"handled_by"}, "in")]
        routes = [ep for ep, sym in sorted(pairs, key=lambda p: p[1] not in matched)]
        tests = tests_covering(project, f)
        users = [n["id"] for n in graph.neighbors(project, f, {"imports"}, "in") if not _is_test(n["id"])]
        completeness = (1 if routes else 0) + (1 if tests else 0) + (0.5 if users else 0)
        if routes and re.search(r"\b(endpoint|api|route|webhook|handler)\b", task, re.I):
            completeness += 1.5
        verbs = set(re.findall(r"[a-z]+", task.lower()))
        if any(v in h.id.lower() for h in hs for v in verbs if len(v) > 3):
            completeness += 0.5
        score = max(h.score for h in hs) * (1 + completeness)
        anatomy = [{"id": f, "text": f"implementation: {_path(f)}", "why": "the file to read first"}]
        anatomy += [{"id": s.id, "text": f"symbol: {s.text[:120]}", "why": "closest match"} for s in hs[:2]]
        anatomy += [{"id": r, "text": f"endpoint: {_label(project, r)}", "why": "how it's exposed"} for r in routes[:2]]
        anatomy += [{"id": t, "text": f"tests: {_path(t)}", "why": "test pattern to copy"} for t in tests[:2]]
        anatomy += [{"id": u, "text": f"registered in: {_path(u)}", "why": "where it's wired up"} for u in users[:2]]
        cands.append((score, f, anatomy))
    cands.sort(key=lambda c: -c[0])
    return [{"title": f"Precedent: {_path(f)}", "items": anatomy} for _, f, anatomy in cands[:2]]


def conventions(project: str) -> list[dict]:
    files = [r["label"] for r in db.q("SELECT label FROM nodes WHERE project=? AND type='file'", (project,))]
    layers = defaultdict(Counter)
    for path in files:
        for part in path.split("/")[:-1]:
            if part in LAYER_NAMES:
                prefix = path.split(part)[0] + part
                layers[LAYER_NAMES[part]][prefix] += 1
                break
    items = [{"id": f"dir:{d}", "text": f"{role}: {d}/ ({n} files)", "why": "repository layering"}
             for role, dirs in layers.items() for d, n in dirs.most_common(1)]
    return [{"title": "Conventions", "items": sorted(items, key=lambda i: i["text"])}] if items else []


def impact(project: str, target: str) -> list[dict]:
    nid = target
    if not graph.node(project, nid):
        hits = seeds(project, target, k=1)
        if not hits:
            return []
        nid = hits[0].id
    br = graph.blast_radius(project, [nid], max_depth=3)
    g = br["groups"]
    sections = []

    def add(title, rows, why, n=6):
        if rows:
            sections.append({"title": title, "items": [{"id": r["id"], "text": r["label"], "why": why} for r in rows[:n]]})
    add("Direct", [r for r in g.get("symbol", []) if r["depth"] == 1], "calls or is called by the change")
    add("API", g.get("endpoint", []), "endpoint served by affected code")
    add("Data", g.get("field", []), "field read or written by affected code")
    add("Wider code", [r for r in g.get("symbol", []) if r["depth"] > 1], "reached through callers", 5)
    tests = tests_covering(project, nid)
    if tests:
        sections.append({"title": "Tests", "items": [{"id": t, "text": _path(t), "why": "covers the changed code"} for t in tests[:6]]})
    else:
        sections.append({"title": "Tests", "items": [{"id": nid, "text": "No test covers this directly", "why": "add one before changing it"}]})
    f = _file_of(nid)
    if f:
        co = sorted(graph.neighbors(project, f, {"co_changes"}, "out"), key=lambda n: n["id"])
        if co:
            sections.append({"title": "Usually changes with", "items": [
                {"id": c["id"], "text": _path(c["id"]), "why": "changed together in past PRs"} for c in co[:4]]})
        people = Counter()
        for pr in graph.neighbors(project, f, {"touches"}, "in"):
            for p in graph.neighbors(project, pr["id"], {"authored", "reviewed"}, "in"):
                people[p["id"]] += 1
        if people:
            sections.append({"title": "People", "items": [{"id": p, "text": p.split(":", 1)[1], "why": "wrote or reviewed this file"}
                                                            for p, _ in people.most_common(3)]})
    return sections


def explain(project: str, symbol: str) -> list[dict]:
    card = memory_for(project).get(symbol)
    callers = graph.neighbors(project, symbol, {"calls"}, "in")
    callees = graph.neighbors(project, symbol, {"calls"}, "out")
    fields = graph.neighbors(project, symbol, {"uses_field", "writes_field"}, "out")
    items = [{"id": symbol, "text": card["text"] if card else symbol, "why": "definition"}]
    items += [{"id": c["id"], "text": f"called by {_label(project, c['id'])}", "why": "caller"} for c in callers[:4]]
    items += [{"id": c["id"], "text": f"calls {_label(project, c['id'])}", "why": "dependency"} for c in callees[:4]]
    items += [{"id": x["id"], "text": f"{'writes' if x['edge'] == 'writes_field' else 'reads'} {_label(project, x['id'])}", "why": "data"} for x in fields[:4]]
    return [{"title": f"Explain {_label(project, symbol)}", "items": items}]


def context(project: str, task: str, state: dict | None = None, budget: int = 2000) -> dict:
    """Stateful context. state: {inspected: [ids], edited: [ids], failing_tests: [paths]}"""
    state = state or {}
    sections = understand(project, task) + find_precedent(project, task)
    for e in state.get("edited", []):
        callers = [c for c in graph.neighbors(project, e, {"calls"}, "in") if not _is_test(c["id"])]
        tests = tests_covering(project, e)
        f = _file_of(e)
        co = [c["id"] for c in graph.neighbors(project, f, {"co_changes"}, "out")] if f else []
        untouched_co = [c for c in co if c not in state.get("edited", []) and c not in state.get("inspected", [])]
        items = [{"id": c["id"], "text": f"also calls it: {_label(project, c['id'])}", "why": "other caller may break"} for c in callers[:5]]
        items += [{"id": t, "text": f"test to run: {_path(t)}", "why": "covers the edited code"} for t in tests[:4]]
        items += [{"id": c, "text": f"often changes with it: {_path(c)}", "why": "co-changed in past PRs, not opened yet"} for c in untouched_co[:3]]
        if items:
            sections.insert(0, {"title": f"Because you edited {_label(project, e)}", "items": items})
    for t in state.get("failing_tests", []):
        hits = memory_for(project).search("knowledge", t, top_k=3)
        sections.insert(0, {"title": f"Failing: {t}", "items": [{"id": h.id, "text": h.text[:140], "why": "likely related to the failure"} for h in hits]})
    seen = set(state.get("inspected", []))
    return pack(sections, budget, seen)


def pack(sections: list[dict], budget: int, skip: set[str] | None = None) -> dict:
    """Greedy pack by section order; drop items already seen; stop at the budget."""
    out, used, dropped = [], 0, 0
    skip = set(skip or ())
    for s in sections:
        items = []
        for it in s["items"]:
            if it["id"] in skip:
                continue
            cost = _tok(len(it["text"]) + len(it["why"]) + 8)
            if used + cost > budget:
                dropped += 1
                continue
            skip.add(it["id"])
            items.append(it)
            used += cost
        if items:
            out.append({"title": s["title"], "items": items})
    text = "\n\n".join(s["title"].upper() + "\n" + "\n".join(f"- {i['text']}  ({i['why']})" for i in s["items"]) for s in out)
    return {"sections": out, "text": text, "tokens": used, "budget": budget, "dropped": dropped}


# ---------------- evaluation: Context Recall@K ----------------

def evaluate(project: str, n: int = 20, k: int = 10) -> dict:
    """For merged PRs, task = PR title. Gold = code files it touched. Compare what each method surfaces
    before any exploration. The PR's own card is excluded; co-change edges are ignored to limit leakage."""
    prs = []
    for r in db.q("SELECT id, label FROM nodes WHERE project=? AND type='pr'", (project,)):
        gold = {nb["id"] for nb in graph.neighbors(project, r["id"], {"touches"}, "out")
                if re.search(r"\.(py|tsx?|jsx?)$", nb["id"]) and not _is_test(nb["id"])}
        if 1 <= len(gold) <= 12 and graph.node(project, next(iter(gold))):
            prs.append((r["id"], r["label"].split(" ", 1)[-1], gold))
    prs = prs[:n]
    files = [r["id"] for r in db.q("SELECT id FROM nodes WHERE project=? AND type='file'", (project,))]
    methods = {"keyword": _m_keyword, "vector": _m_vector, "engine": _m_engine}
    res = {m: {"recall": 0.0, "precision": 0.0, "tokens": 0} for m in methods}
    for pid, task, gold in prs:
        for m, fn in methods.items():
            ranked, tokens = fn(project, task, files, pid, k)
            got = set(ranked[:k]) & gold
            res[m]["recall"] += len(got) / len(gold)
            res[m]["precision"] += len(got) / max(len(ranked[:k]), 1)
            res[m]["tokens"] += tokens
    for m in res:
        for key in res[m]:
            res[m][key] = round(res[m][key] / max(len(prs), 1), 3 if key != "tokens" else 0)
    return {"tasks": len(prs), "k": k, "results": res,
            "note": "Tasks are PR titles; gold is the code files each PR touched. Measured on the current HEAD, so treat as an upper bound."}


def _words(task):
    return [w for w in re.findall(r"[a-z0-9]+", task.lower()) if len(w) > 3]


def _m_keyword(project, task, files, pr, k):
    words = _words(task)
    scored = sorted(files, key=lambda f: -sum(w in f.lower() for w in words))
    top = [f for f in scored if any(w in f.lower() for w in words)][:k]
    return top, sum(_tok(len(f)) for f in top)


def _m_vector(project, task, files, pr, k):
    hits = memory_for(project).search("knowledge", task, top_k=k * 2)
    out = []
    for h in hits:
        f = _file_of(h.id) or ("file:" + h.metadata.get("repo", "") + ":" + h.metadata.get("file", "") if h.metadata.get("file") else None)
        if f and f not in out:
            out.append(f)
    return out[:k], sum(_tok(len(h.text)) for h in hits[:k])


def rank_files(project: str, task: str, exclude: set[str] | None = None, use_history: bool = True) -> tuple[list[tuple[str, float]], int]:
    """Hybrid file ranking: symbol/endpoint vectors + keyword prior on paths + files that similar past PRs changed
    + light call-graph propagation from the top seeds. Measured by evaluate()."""
    exclude = exclude or set()
    mem = memory_for(project)
    score, toks = Counter(), 0
    hits = [h for h in mem.search("knowledge", task, top_k=24) if h.metadata.get("type") in ("symbol", "endpoint")][:12]
    for rank, h in enumerate(hits):
        b = 1.0 / (1 + rank)
        toks += _tok(len(h.text))
        if h.id.startswith("route:"):
            for nb in graph.neighbors(project, h.id, {"handled_by"}, "out"):
                score[_file_of(nb["id"])] += b
        f = _file_of(h.id)
        if not f:
            continue
        score[f] += b
        if rank < 3:
            for nb in graph.neighbors(project, h.id, {"calls"}, "both"):
                nf = _file_of(nb["id"])
                if nf:
                    score[nf] += b * 0.15
    words = _words(task)
    if words:
        for r in db.q("SELECT id FROM nodes WHERE project=? AND type='file'", (project,)):
            n = sum(w in r["id"].lower() for w in words)
            if n:
                score[r["id"]] += 0.6 * n / len(words)
    if use_history:
        prs = [h for h in mem.search("changes", task, top_k=6) if h.id not in exclude and h.metadata.get("type") == "pr"][:4]
        for rank, h in enumerate(prs):
            toks += _tok(len(h.text))
            for nb in graph.neighbors(project, h.id, {"touches"}, "out"):
                score[nb["id"]] += 0.5 / (1 + rank)
    ranked = [(f, sc) for f, sc in score.most_common() if f and not _is_test(f) and re.search(r"\.(py|tsx?|jsx?|go|java|rb)$", f)]
    return ranked, toks


def _m_engine(project, task, files, pr, k):
    ranked, toks = rank_files(project, task, exclude={pr})
    return [f for f, _ in ranked[:k]], toks
