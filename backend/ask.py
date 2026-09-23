"""Search for people: "where does X live in this project?" answered in plain words, with readable results.

The raw index (cards, symbols, PR text) is what agents search. People need three things instead: a one-line answer,
results with human titles grouped by what they are, and honesty when nothing actually matches the words they typed.
"""
from __future__ import annotations

import json
import re

from . import areas, db, gateway, graph
from .memory import memory_for

KIND = {"symbol": "Code", "code": "Code", "endpoint": "Requests", "field": "Data", "table": "Data", "mapping": "Data",
        "pr": "Changes", "issue": "Changes", "deploy": "Changes", "doc": "Docs", "brief": "From the brief", "flow": "How it's used",
        "lesson": "What seniors said", "person": "People"}
ORDER = ["How it's used", "From the brief", "Requests", "Code", "Data", "Changes", "Docs", "What seniors said", "People"]


STOP = {"who", "what", "where", "when", "which", "how", "why", "the", "and", "for", "does", "are", "is", "this", "that", "with", "from", "our", "can"}


def _words(q: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9_]{3,}", q.lower()) if w not in STOP]


def _stem(w: str) -> str:
    return re.sub(r"(ing|ed|es|s)$", "", w) if len(w) > 4 else w


def _file_of(hit_id: str, meta: dict) -> str | None:
    if hit_id.startswith("sym:"):
        return hit_id[4:].split("#")[0].split(":", 1)[-1]
    if hit_id.startswith(("doc:", "file:")):
        return hit_id.split(":", 2)[-1].split("#")[0]
    return meta.get("file")


def _present(pid: str, h, area_of: dict) -> dict | None:
    meta, text, hid = h.metadata or {}, h.text, h.id
    t = meta.get("type") or hid.split(":", 1)[0]
    kind = KIND.get(t)
    if not kind or t == "person":
        return None
    title, sub = hid, ""
    if t == "symbol" or hid.startswith("sym:"):
        name = hid.split("#")[-1]
        f = _file_of(hid, meta) or ""
        title = f"{name}()" if "class" not in text.split(":")[0] else name
        if re.search(r"(^|/)(migrations|alembic/versions|db/migrate)/", f):
            mig = f.split("/")[-1].rsplit(".", 1)[0]
            if name in ("downgrade",):
                return None  # the undo half of a migration adds nothing a person needs
            title, sub = f"Migration {mig}", "changes the database schema"
            return {"id": hid, "kind": "Data", "title": title, "sub": sub, "area": None, "text": text}
        sub = f"in {f.split('/')[-1]}" + (f". {m.group(1)}" if (m := re.search(r"\):\s*([A-Z][^.]{10,120}\.)", text)) else "")
    elif t == "endpoint":
        title = text.replace("Endpoint ", "").split(" in ")[0]
        sub = "a request the app serves"
    elif t in ("field", "table", "mapping"):
        title = hid.split(":", 1)[-1]
        sub = "a column" if "." in title else "a table"
    elif t == "pr":
        n = graph.node(pid, hid) or {"label": hid, "props": {}}
        title = f"PR {n['label']}"
        sub = ("merged" if n["props"].get("merged_at") else "open") + (f", by {meta.get('author')}" if meta.get("author") else "")
    elif t == "issue":
        title = f"Issue #{meta.get('number', '')}: {meta.get('title', '')}"
        sub = meta.get("state", "")
    elif t == "doc":
        path, _, rest = text.partition(" / ")
        section, _, body = rest.partition(":")
        title = f"{path.split('/')[-1].rsplit('.', 1)[0].capitalize()}: {section.strip()}"
        sub = body.strip()[:140]
    elif t == "brief":
        title = text.split("?")[0] + "?"
        sub = text.split("?", 1)[-1].strip()[:160]
    elif t in ("flow", "lesson"):
        title = text.split(".")[0][:90]
        sub = text[len(title):].strip(" .")[:160]
    f = _file_of(hid, meta)
    area = area_of.get(f) if f else None
    if t == "endpoint":
        area = next((a for a, eps in area_of.get("__eps__", {}).items() if title in eps), area)
    return {"id": hid, "kind": kind, "title": title, "sub": sub, "area": area, "text": text}


def ask(pid: str, q: str, user: str | None = None) -> dict:
    from . import tracks
    project = db.one("SELECT name FROM projects WHERE id=?", (pid,))
    mem = memory_for(pid)
    see = tracks.visible_tracks(pid, user) if user else None
    hits = [h for h in mem.search(["knowledge", "changes", "experts"], q, top_k=30) if tracks.allowed(h.metadata, see)]
    amap = areas.build(pid)
    area_of: dict = {}
    owners: dict[str, set] = {}
    for a in amap["areas"]:
        for f in a.get("file_paths", []):
            owners.setdefault(f, set()).add(a["name"])
    # a file used by one or two areas is labelled with them; one used everywhere (models.py) is shared plumbing
    area_of.update({f: " · ".join(sorted(n)) for f, n in owners.items() if len(n) <= 2})
    area_of["__eps__"] = {a["name"]: a["endpoints"] for a in amap["areas"]}
    words = [_stem(w) for w in _words(q)]
    rows, seen = [], set()
    for h in hits:
        r = _present(pid, h, area_of)
        if not r or r["title"] in seen:
            continue
        seen.add(r["title"])
        low = (r["title"] + " " + r["text"]).lower()
        r["exact"] = bool(words) and any(w in low for w in words)
        rows.append(r)
    exact = [r for r in rows if r["exact"]]
    shown = exact[:12] if exact else rows[:6]
    # areas the matches point at, most first
    counts: dict[str, int] = {}
    for r in shown:
        if r["area"]:
            counts[r["area"]] = counts.get(r["area"], 0) + 1
    groups = []
    for k in ORDER:
        items = [{kk: r[kk] for kk in ("id", "kind", "title", "sub", "area")} for r in shown if r["kind"] == k]
        if items:
            groups.append({"kind": k, "items": items})
    return {"q": q, "exact": bool(exact), "answer": _answer(project["name"] if project else "this project", q, shown, bool(exact)),
            "areas": sorted(counts, key=lambda a: -counts[a]), "groups": groups}


def _answer(name: str, q: str, rows: list[dict], exact: bool) -> str:
    """One or two plain sentences: where this lives, or that it doesn't. Model if online, else built from the matches."""
    if not rows:
        return f"Nothing in {name} mentions “{q}” yet."
    listing = "\n".join(f"- [{r['kind']}] {r['title']} ({r['area'] or 'no area'}): {r['sub']}" for r in rows[:10])
    try:
        how = ("These matches contain the words they searched for. In one or two short sentences, say where this lives "
               f"in {name}: name the area and the main file or change.") if exact else \
              (f"None of these matches contain their words. Start by saying {name} has nothing called that, "
               "then in one sentence point to the closest area or file.")
        out = gateway.llm_call("fast", "You explain a codebase to someone new to it. Plain words, no jargon, no markdown. Use only the matches given, "
                               "and copy names, file names and PR numbers exactly as they appear there. Never invent a number.",
                               f"Project: {name}\nThey searched for: {q}\nMatches:\n{listing}\n{how}\n" + 'Reply JSON {"answer": "..."}', 160)
        if isinstance(out, dict) and out.get("answer"):
            return out["answer"].strip()
    except Exception:
        pass
    places = [r["area"] for r in rows if r["area"]]
    top = max(set(places), key=places.count) if places else None
    if not exact:
        return f"Nothing in {name} is called “{q}”." + (f" The closest things are in {top}." if top else "")
    return f"“{q.capitalize()}” shows up mostly in {top}." if top else f"Found {len(rows)} places that mention “{q}”."
