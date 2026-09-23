"""Tracks: teams inside one project (Ops, Sales, Product, Platform...), each with its own slice of context.

Two layers of context per project:
  shared  the code, schema, history, context map, project brief and org experts. Everyone sees the same system.
  track   what one team knows: its brief answers, notes and runbooks, and the areas it owns on the context map.
Every card a track adds is tagged with it. People see shared plus their own tracks (leads and owners see all). Agents
working on something see shared plus the track that owns it.

Ownership drives routing: an incident or bug in an area goes to the track that owns that area, and a release lists the
tracks whose areas it changes, so the teams affected hear about it. Business tracks (sales, ops, support) get plain
status and client-ready wording instead of code.
"""
from __future__ import annotations

import json
import re
import time

from . import db

db.x("""CREATE TABLE IF NOT EXISTS tracks(id TEXT PRIMARY KEY, project TEXT, name TEXT, kind TEXT, about TEXT DEFAULT '', created REAL)""")
db.x("CREATE TABLE IF NOT EXISTS track_members(track TEXT, user TEXT, PRIMARY KEY(track, user))")
db.x("CREATE TABLE IF NOT EXISTS track_areas(track TEXT, area TEXT, PRIMARY KEY(track, area))")
db.x("""CREATE TABLE IF NOT EXISTS track_notes(id TEXT PRIMARY KEY, track TEXT, project TEXT, title TEXT, text TEXT, by TEXT, at REAL)""")
for t, col in [("bugs", "track"), ("incidents", "track")]:
    try:
        db.x(f"ALTER TABLE {t} ADD COLUMN {col} TEXT")
    except Exception:
        pass

KINDS = {"engineering": "Engineering", "business": "Business"}

# What each kind of team knows that nobody else writes down.
QUESTIONS = {
    "engineering": [
        ("owns", "What does this team build and look after?", "e.g. Checkout, payments and everything customers pay through."),
        ("oncall", "Who is on call for this team, and how do you reach them?", "e.g. Weekly rotation; page the on-call in #ops-alerts."),
        ("fragile", "What breaks most often in your areas, and what usually fixes it?", "e.g. The nightly import; re-running it by hand usually fixes it."),
        ("never", "What must never be changed without this team?", "e.g. The pricing rules and anything that moves money."),
    ],
    "business": [
        ("customers", "What do customers ask you about most?", "e.g. Where is my order, why was I charged twice, how do refunds work."),
        ("cost", "Which failures cost you the most?", "e.g. Checkout down at peak: lost sales and refunds. A slow report costs nothing."),
        ("promises", "What have you promised customers or clients that the product must keep?", "e.g. Orders confirmed within a minute; refunds within 3 days."),
        ("tell", "Who do you tell when something breaks, and how quickly?", "e.g. Top 20 clients by phone within 30 minutes; everyone else by email."),
        ("words", "Words your team uses that engineers might not?", "e.g. 'Stuck order' means paid for but never confirmed."),
    ],
}


def create(project: str, name: str, kind: str, about: str, by: str) -> str:
    tid = db.uid("trk")
    db.x("INSERT INTO tracks VALUES(?,?,?,?,?,?)", (tid, project, name.strip(), kind if kind in KINDS else "engineering", about.strip(), time.time()))
    db.x("INSERT OR IGNORE INTO track_members VALUES(?,?)", (tid, by))
    return tid


def listing(project: str) -> list[dict]:
    from .pipeline import name
    out = []
    for t in db.q("SELECT * FROM tracks WHERE project=? ORDER BY created", (project,)):
        members = [r["user"] for r in db.q("SELECT user FROM track_members WHERE track=?", (t["id"],))]
        areas = [r["area"] for r in db.q("SELECT area FROM track_areas WHERE track=?", (t["id"],))]
        answered = db.one("SELECT count(*) n FROM brief WHERE project=? AND qid LIKE ? AND answer IS NOT NULL", (project, f"{t['id']}/%"))["n"] \
            if db.q("SELECT name FROM sqlite_master WHERE name='brief'") else 0
        out.append({**t, "members": [{"id": m, "name": name(m)} for m in members], "areas": areas,
                    "notes": db.one("SELECT count(*) n FROM track_notes WHERE track=?", (t["id"],))["n"],
                    "answered": answered, "questions": len(QUESTIONS[t["kind"]])})
    return out


def of_user(project: str, user: str) -> list[str]:
    return [r["track"] for r in db.q("SELECT m.track FROM track_members m JOIN tracks t ON t.id=m.track WHERE t.project=? AND m.user=?", (project, user))]


def visible_tracks(project: str, user: str) -> set[str] | None:
    """None means everything (owners and leads); otherwise the tracks this person's context searches may include."""
    if db.role_of(user, project) in ("owner", "lead"):
        return None
    return set(of_user(project, user))


def set_areas(track: str, areas: list[str]):
    """An area belongs to one track: giving it to this track takes it from any other in the project."""
    t = db.one("SELECT project FROM tracks WHERE id=?", (track,))
    others = [r["id"] for r in db.q("SELECT id FROM tracks WHERE project=? AND id<>?", (t["project"], track))]
    for a in areas:
        for o in others:
            db.x("DELETE FROM track_areas WHERE track=? AND area=?", (o, a))
    db.x("DELETE FROM track_areas WHERE track=?", (track,))
    db.xmany("INSERT OR IGNORE INTO track_areas VALUES(?,?)", [(track, a) for a in areas])


def set_members(track: str, users: list[str]):
    db.x("DELETE FROM track_members WHERE track=?", (track,))
    db.xmany("INSERT OR IGNORE INTO track_members VALUES(?,?)", [(track, u) for u in users])


def owner_of_area(project: str, area: str) -> dict | None:
    return db.one("SELECT t.* FROM track_areas a JOIN tracks t ON t.id=a.track WHERE t.project=? AND a.area=?", (project, area))


def areas_of_files(project: str, files: list[str]) -> list[str]:
    from . import areas
    amap = areas.build(project)
    hits: dict[str, int] = {}
    for a in amap["areas"]:
        n = sum(1 for f in files if f and f in a.get("file_paths", []))
        if n:
            hits[a["id"]] = n
    return sorted(hits, key=lambda k: -hits[k])


def route(project: str, files: list[str]) -> dict | None:
    """The track that owns most of the code something touches."""
    for area in areas_of_files(project, files):
        t = owner_of_area(project, area)
        if t:
            return t
    return None


def affected_by_release(release: dict) -> list[dict]:
    """Tracks whose areas a release changes, with the areas and why."""
    from .releases import changed_symbols
    files = sorted({fid.split(":", 2)[-1] for fid in changed_symbols(release)})
    areas = areas_of_files(release["project"], files)
    out: dict[str, dict] = {}
    for a in areas:
        t = owner_of_area(release["project"], a)
        if t:
            out.setdefault(t["id"], {"track": t["id"], "name": t["name"], "kind": t["kind"], "areas": []})["areas"].append(a)
    return list(out.values())


# ---------------- track context ----------------

def questions(project: str, track: str) -> list[dict]:
    t = db.one("SELECT * FROM tracks WHERE id=?", (track,))
    now = time.time()
    for qid, q, hint in QUESTIONS[t["kind"]]:
        db.x("INSERT OR IGNORE INTO brief(project, qid, kind, question, hint, created) VALUES(?,?,?,?,?,?)",
             (project, f"{track}/{qid}", "track", q, hint, now))
    rows = db.q("SELECT * FROM brief WHERE project=? AND qid LIKE ? ORDER BY created, qid", (project, f"{track}/%"))
    order = [q[0] for q in QUESTIONS[t["kind"]]]
    rows.sort(key=lambda r: order.index(r["qid"].split("/", 1)[1]) if r["qid"].split("/", 1)[1] in order else 99)
    for r in rows:
        r["choices"] = json.loads(r["choices"] or "[]")
    return rows


def answer(project: str, track: str, qid: str, text: str, by: str):
    from .context import save_cards
    full = f"{track}/{qid}" if "/" not in qid else qid
    row = db.one("SELECT question FROM brief WHERE project=? AND qid=?", (project, full))
    if not row:
        raise KeyError(qid)
    t = db.one("SELECT name FROM tracks WHERE id=?", (track,))
    db.x("UPDATE brief SET answer=?, answered_by=?, answered_at=? WHERE project=? AND qid=?", (text.strip() or None, by, time.time(), project, full))
    if text.strip():
        save_cards(project, "knowledge", [{"id": f"brief:{full}", "text": f"{t['name']} team: {row['question']} {text.strip()}",
                                           "metadata": {"type": "brief", "track": track, "question": full, "by": by}}])


def add_note(project: str, track: str, title: str, text: str, by: str) -> str:
    """Runbooks, decisions, glossaries: split into sections so each can be cited."""
    from .context import save_cards
    from .org import _sections
    nid = db.uid("tn")
    db.x("INSERT INTO track_notes VALUES(?,?,?,?,?,?,?)", (nid, track, project, title.strip() or "Note", text, by, time.time()))
    t = db.one("SELECT name FROM tracks WHERE id=?", (track,))
    cards = [{"id": f"tnote:{nid}#{i}", "text": f"{t['name']} team, {sec}: {body}", "metadata": {"type": "doc", "track": track, "by": by, "file": title}}
             for i, (sec, body) in enumerate(_sections(title, text))]
    save_cards(project, "knowledge", cards)
    return nid


def remove_note(project: str, track: str, nid: str):
    db.x("DELETE FROM track_notes WHERE id=? AND track=?", (nid, track))
    db.x("DELETE FROM cards WHERE project=? AND id LIKE ?", (project, f"tnote:{nid}#%"))


def summary(project: str, track: str, limit: int = 800) -> str:
    """What this team told us, for agents and Jev when the work belongs to it."""
    t = db.one("SELECT name, kind FROM tracks WHERE id=?", (track,))
    rows = db.q("SELECT question, answer FROM brief WHERE project=? AND qid LIKE ? AND answer IS NOT NULL", (project, f"{track}/%"))
    if not t or not rows:
        return ""
    return (f"The {t['name']} team says: " + " ".join(f"{r['question']} {r['answer']}" for r in rows))[:limit]


def allowed(meta: dict, tracks: set[str] | None) -> bool:
    """Search visibility: untagged cards are shared; tagged ones need the track (None = see everything)."""
    tr = (meta or {}).get("track")
    return tracks is None or not tr or tr in tracks


# ---------------- a track's own view ----------------

def status(project: str, track: str) -> dict:
    """What this team needs to know today, in plain words."""
    from . import areas as area_map
    from .pipeline import name
    t = db.one("SELECT * FROM tracks WHERE id=?", (track,))
    owned = {r["area"] for r in db.q("SELECT area FROM track_areas WHERE track=?", (track,))}
    amap = {a["id"]: a for a in area_map.build(project)["areas"]}
    files = {f for a in owned for f in (amap.get(a) or {}).get("file_paths", [])}
    incidents = []
    for i in db.q("SELECT id, title, status, severity, opened_at, evidence, track FROM incidents WHERE project=? AND status IN ('proposed','open','mitigating') ORDER BY opened_at DESC", (project,)):
        code = " ".join((json.loads(i["evidence"] or "{}").get("context") or {}).get("code", []))
        if i["track"] == track or any(f in code for f in files):
            incidents.append({k: i[k] for k in ("id", "title", "status", "severity", "opened_at")})
    bugs = [dict(b) for b in db.q("SELECT id, title, stage, priority, release FROM bugs WHERE project=? AND track=? AND stage<>'verified' ORDER BY created DESC", (project, track))]
    releases = []
    for r in db.q("SELECT * FROM releases WHERE project=? AND stage NOT IN ('closed')", (project,)):
        hit = [x for x in affected_by_release(r) if x["track"] == track]
        if hit:
            releases.append({"id": r["id"], "name": r["name"], "stage": r["stage"], "window": r["window_start"], "areas": hit[0]["areas"]})
    parts = []
    if incidents:
        parts.append(f"{len(incidents)} problem{'s' if len(incidents) > 1 else ''} in production affect{'' if len(incidents) > 1 else 's'} your areas right now.")
    if bugs:
        parts.append(f"{len(bugs)} bug{'s' if len(bugs) > 1 else ''} in your areas {'are' if len(bugs) > 1 else 'is'} being worked on.")
    if releases:
        parts.append(f"{releases[0]['name']} changes {', '.join(amap[a]['name'] for a in releases[0]['areas'] if a in amap)}"
                     + (f" and {len(releases) - 1} more release{'s' if len(releases) > 2 else ''} touch your areas." if len(releases) > 1 else "."))
    sentence = " ".join(parts) or ("Nothing affecting your areas right now." if owned else "Pick the areas this team owns to see what affects it.")
    return {"track": {**t, "members": [{"id": m["user"], "name": name(m["user"])} for m in db.q("SELECT user FROM track_members WHERE track=?", (track,))]},
            "sentence": sentence, "areas": [{"id": a, "name": amap[a]["name"] if a in amap else a} for a in sorted(owned)],
            "incidents": incidents, "bugs": bugs, "releases": releases,
            "notes": db.q("SELECT id, title, by, at FROM track_notes WHERE track=? ORDER BY at DESC", (track,))}


def client_update(project: str, track: str, incident_id: str) -> str:
    """A plain message this team can send customers or a client about a live problem. No internals, no blame, no guesses."""
    from . import gateway
    i = db.one("SELECT * FROM incidents WHERE id=? AND project=?", (incident_id, project))  # never another project's incident
    t = db.one("SELECT name FROM tracks WHERE id=?", (track,))
    if not i or not t:
        raise LookupError("No such incident in this project")
    words = summary(project, track, 600)
    fallback = (f"We're aware of a problem affecting part of the service and our team is working on it. "
                f"We'll update you as soon as it's resolved.")
    try:
        r = gateway.llm_call("fast", "You write short customer-facing status updates. Plain, calm, honest. No technical terms, no internal names, "
                             "no blame, no promised times unless given. Two or three sentences.",
                             f"Team sending it: {t['name']}\nWhat the team told us: {words or 'nothing yet'}\nProblem (internal wording): {i['title']}\n"
                             f"Status: {i['status']}\n" + 'Reply JSON {"update": "..."}', 160)
        return (r.get("update") or fallback).strip() if isinstance(r, dict) else fallback
    except Exception:
        return fallback
