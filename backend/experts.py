"""Expert agents: a senior's judgement for one area of the code, available when the seniors aren't.

What makes one an expert is not its prompt. It is three things, all inspectable:
  1. A knowledge base scoped to its area, built from what seniors actually wrote and did:
       - review comments seniors left on PRs in the area (where judgement is written down)
       - bugs in the area that were fixed and verified, with their cause
       - incidents resolved in the area, with what fixed them
       - pinned decisions, and lessons people teach it directly
  2. Who it learned from, by name, with how much each person contributed.
  3. A track record: how often people followed its advice, and whether the fix held.

It advises and flags. It never approves: risky changes still get a named senior's sign-off.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import db, gateway, graph
from .memory import memory_for

db.con.executescript("""
CREATE TABLE IF NOT EXISTS experts(id TEXT PRIMARY KEY, project TEXT, name TEXT, paths TEXT, built REAL, teachers TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS lessons(id TEXT PRIMARY KEY, project TEXT, expert TEXT, text TEXT, taught_by TEXT, source TEXT, ref TEXT, at REAL);
CREATE TABLE IF NOT EXISTS advice(id TEXT PRIMARY KEY, project TEXT, expert TEXT, owner_type TEXT, owner_id TEXT, text TEXT, cites TEXT,
  signoff TEXT, verdict TEXT DEFAULT 'pending', verdict_by TEXT, at REAL);
""")
try:
    db.con.execute("ALTER TABLE members ADD COLUMN level TEXT")  # junior | mid | senior | staff, set by a lead
    db.con.commit()
except Exception:
    pass

LEVELS = ["junior", "mid", "senior", "staff"]
NOISE = re.compile(r"^(lgtm|looks good|thanks|thank you|nice|approved?|\+1|ship it|great)[!. ]*$", re.I)
# Changes here are expensive to get wrong; a senior signs off even when the expert is confident.
RISKY = re.compile(r"migration|alembic|models?\.py|schema\.(prisma|sql)|auth|security|permission|payment|billing|crud\.py", re.I)
RISK_WORD = [(re.compile(r"migration|alembic", re.I), "a migration"), (re.compile(r"models?\.py|schema\.", re.I), "the data model"),
             (re.compile(r"auth|security|permission", re.I), "auth"), (re.compile(r"payment|billing", re.I), "payments"),
             (re.compile(r"crud\.py", re.I), "shared data access")]


def risk_words(files: list[str]) -> list[str]:
    code = [f for f in files if f and not f.split("/")[-1].startswith(".") and not f.endswith((".md", ".txt", ".yml", ".yaml", ".json", ".lock"))
            and "/test" not in f and not f.split("/")[-1].startswith("test_")]
    return [w for rx, w in RISK_WORD if any(rx.search(f) for f in code)]
MIN_PRS = 5      # history an area needs before it gets an expert
SENIOR_PRS = 3   # PRs written or reviewed in an area that make someone senior there, unless a lead says otherwise
BOTS = {"copilot", "github-actions", "dependabot", "pre-commit-ci", "codecov", "vercel", "netlify", "renovate", "sonarcloud", "coderabbitai"}
# Review comments worth learning from say what to do or why, not just "thanks".
SUBSTANCE = re.compile(r"`|\b(should|shouldn't|must|don't|do not|instead|because|avoid|need to|make sure|careful|breaks?|won't|prefer|rather|why|null|index|migrat|test)\b", re.I)


def _area(path: str) -> str | None:
    """One expert per top-level part of the repo (backend, frontend, packages/x). Overlapping experts just argue."""
    parts = path.split("/")
    if len(parts) < 2 or parts[0].startswith(".") or parts[0] in ("docs", "img", "scripts", "test", "tests"):
        return None
    if parts[0] in ("packages", "apps", "services", "libs") and len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0]


def _clean(txt: str) -> str:
    """Keep what the reviewer said, drop pasted code and collapsed logs."""
    txt = re.sub(r"<details>.*?</details>", " ", txt, flags=re.S | re.I)
    txt = re.sub(r"```.*?```", " (code) ", txt, flags=re.S)
    txt = re.sub(r"```.*$", " (code)", txt, flags=re.S)  # cut off mid-block
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _label(area: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[/_\-]", area) if w) + " expert"


def _login_user(project: str, login: str) -> dict | None:
    return db.one("SELECT u.id, u.name, m.role, m.level FROM users u JOIN members m ON m.user=u.id AND m.project=? WHERE u.github=?", (project, login))


# ---------------- building the knowledge base ----------------

RULE = re.compile(r"\b(must|never|always|don't|do not|shouldn't|should not|because|so that|otherwise|only if|make sure|careful|avoid)\b", re.I)
COMMENT = re.compile(r"^\s*(?:#|//|--)\s?(.*)$")


def _repo_root(project: str) -> Path | None:
    from .github import CLONES
    r = db.one("SELECT full_name FROM repos WHERE project=? ORDER BY synced DESC", (project,))
    if not r:
        return None
    p = CLONES / project / r["full_name"].replace("/", "__")
    return p if p.exists() else None


def _code_rules(root: Path, files: list[str]) -> list[tuple[str, str]]:
    """Comments and docstring lines that state a rule (must, never, because...). The code's own seniors, in writing."""
    out = []
    for f in files:
        p = root / f
        if not p.exists() or p.stat().st_size > 400_000:
            continue
        lines = p.read_text(errors="ignore").splitlines()
        for n, line in enumerate(lines, 1):
            m = COMMENT.match(line) or re.search(r"\s#\s?(.{25,})$", line)
            text = (m.group(1) if m else "").strip()
            if not text and '"""' in line:
                text = line.strip().strip('"').strip()
            if len(text) >= 25 and RULE.search(text) and not text.lower().startswith(("noqa", "type:", "todo")):
                out.append((f"{text} ({f.split('/')[-1]}:{n})", f"{f}:{n}"))
    return out


def _test_rules(project: str, area_files: set[str], area_words: set[str]) -> list[tuple[str, str]]:
    """Test names are promises: test_totals_apply_gst_after_discount -> 'Totals apply GST after discount'."""
    out = []
    for r in db.q("SELECT id, label FROM nodes WHERE project=? AND type='symbol' AND label LIKE 'test_%'", (project,)):
        f = r["id"][4:].split("#")[0].split(":", 1)[-1]
        name = r["id"].split("#")[-1]
        words = set(name.lower().split("_")[1:])
        called = {c["id"][4:].split("#")[0].split(":", 1)[-1] for c in graph.neighbors(project, r["id"], {"calls"}, "out")}
        if called & area_files or words & area_words:
            sentence = name[5:].replace("_", " ").strip()
            out.append((f"Tested rule: {sentence[:1].upper()}{sentence[1:]} ({f.split('/')[-1]})", r["id"]))
    return out


def _doc_rules(project: str, area_files: set[str], area_words: set[str]) -> list[tuple[str, str]]:
    """Doc sections that talk about this area's files, tables or name."""
    out = []
    for c in _cards(project, "knowledge"):
        if c["metadata"].get("type") != "doc":
            continue
        text = c["text"]
        low = text.lower()
        hits = sum(1 for f in area_files if f.split("/")[-1] in text) + sum(1 for w in area_words if re.search(rf"\b{re.escape(w)}", low))
        if hits >= 2:
            out.append((text[:600], c["id"]))
    return out


def build(project: str, max_experts: int = 8) -> list[dict]:
    """One expert per area of the context map (Orders, Coupons...), taught from everything the project has written down:
    review comments, the authors' own notes and PR descriptions, rule comments in the code, docs, tests, the brief,
    fixes that held, war rooms, and what people teach it directly."""
    from . import areas as area_map
    amap = area_map.build(project)
    root = _repo_root(project)
    cards = {c["id"]: c for c in _cards(project, "changes")}
    brief_rows = db.q("SELECT qid, question, answer, answered_by FROM brief WHERE project=? AND answer IS NOT NULL AND qid IN "
                      "('rules','critical','sensitive','broken')", (project,)) if db.q("SELECT name FROM sqlite_master WHERE name='brief'") else []
    candidates = [a for a in amap["areas"] if a["functions"] >= 2][:max_experts]
    old = {r["id"] for r in db.q("SELECT id FROM experts WHERE project=?", (project,))}
    out, kept = [], set()
    for a in candidates:
        files = [f for f in a.get("file_paths", []) if not re.search(r"(^|/)tests?/", f)]
        if not files:
            continue
        eid = f"exp_{project[-6:]}_{re.sub(r'[^a-z0-9]', '', a['id'].lower())[:24]}"
        kept.add(eid)
        area_files = set(files)
        area_words = {a["id"].lower().rstrip("s"), *[t.lower() for t in a["tables"]]}
        teachers: Counter = Counter()
        lessons: list[tuple] = []

        def add(text, who, src, ref):
            text = text.strip()
            if len(text) >= 20 and not any(t == text[:600] for t, *_ in lessons):
                lessons.append((text[:600], who, src, ref))
                if who not in ("the code", "docs", "tests", "war room", "team"):
                    teachers[who] += 1

        prs = {r["pr"] for r in db.q(f"SELECT DISTINCT pr FROM pr_files WHERE project=? AND path IN ({','.join('?' * len(files))})", (project, *files))}
        for pr in prs:
            c = cards.get(pr)
            if not c:
                continue
            author = c["metadata"].get("author", "")
            notes = [(r["who"], r["text"]) for r in db.q("SELECT who, text FROM reviews WHERE project=? AND pr=?", (project, pr))] or _review_notes(c["text"])
            for who, txt in notes:
                txt = _clean(txt)
                if who.endswith("[bot]") or who.lower() in BOTS or len(txt) < 40 or NOISE.match(txt) or not SUBSTANCE.search(txt):
                    continue
                add(txt, who, "author_note" if who == author else "review", pr)  # an author explaining their own change still counts
            body = c["text"].split(". Files:")[0]
            body = re.sub(r"^PR #\d+ .*? by [\w-]+(, reviewed by [\w, -]+)?\. ", "", body)
            if len(body) > 60 and SUBSTANCE.search(body):
                add(f"Why {c['text'].split(' by ')[0]} was made: {body}", author or "team", "pr", pr)
        if root:
            for text, ref in _code_rules(root, files):
                add(text, "the code", "code", ref)
        for text, ref in _doc_rules(project, area_files, area_words):
            add(text, "docs", "doc", ref)
        for text, ref in _test_rules(project, area_files, area_words):
            add(text, "tests", "test", ref)
        for b in brief_rows:
            add(f"{b['question']} {b['answer']}", b["answered_by"] or "team", "brief", f"brief:{b['qid']}")
        for b in db.q("SELECT id, title, evidence, assignee FROM bugs WHERE project=? AND stage='verified'", (project,)):
            ev = json.loads(b["evidence"]) if (b["evidence"] or "").startswith("{") else {}
            if {graph_file(c) for s in ev.get("steps", []) for c in s.get("cites", [])} & area_files:
                who = db.one("SELECT github, name FROM users WHERE id=?", (b["assignee"],)) or {}
                add(f"Bug '{b['title']}' was caused by: {(ev.get('theory') or {}).get('text', '')[:300]} Fixed by {who.get('name', 'the team')}, verified on staging.",
                    who.get("github") or who.get("name") or "team", "fix", b["id"])
        for i in db.q("SELECT id, title, evidence FROM incidents WHERE project=? AND status='resolved'", (project,)):
            code = " ".join((json.loads(i["evidence"] or "{}").get("context") or {}).get("code", []))
            if any(f in code for f in files):
                res = db.one("SELECT note FROM (SELECT json_extract(value,'$.note') note, json_extract(value,'$.step') step FROM incidents, "
                             "json_each(incidents.relay) WHERE incidents.id=?) WHERE step='resolved'", (i["id"],))
                add(f"War room '{i['title']}' was resolved: {(res or {}).get('note') or 'no note'}", "war room", "incident", i["id"])
        # rebuild everything learned from the project; keep what people taught by hand or in threads
        db.x("DELETE FROM lessons WHERE expert=? AND source NOT IN ('manual','thread')", (eid,))
        db.xmany("INSERT INTO lessons VALUES(?,?,?,?,?,?,?,?)", [(db.uid("les"), project, eid, t, who, src, ref, time.time()) for t, who, src, ref in lessons])
        db.x("INSERT OR REPLACE INTO experts VALUES(?,?,?,?,?,?)",
             (eid, project, f"{a['name']} expert", json.dumps(files), time.time(), json.dumps(teachers.most_common(6))))
        _index(project, eid)
        out.append({"id": eid, "area": a["name"], "lessons": len(lessons), "by_source": dict(Counter(l[2] for l in lessons))})
    for gone in old - kept:  # experts for folders that no longer exist as areas
        if not db.one("SELECT 1 FROM lessons WHERE expert=? AND source IN ('manual','thread')", (gone,)):
            db.x("DELETE FROM lessons WHERE expert=?", (gone,))
            db.x("DELETE FROM experts WHERE id=?", (gone,))
    db.event(project, None, "context", "experts_built", {"n": len(out)})
    return out


def graph_file(cite: str) -> str | None:
    if cite.startswith("sym:"):
        return cite[4:].split("#")[0].split(":", 1)[-1]
    if cite.startswith("file:"):
        return cite.split(":", 2)[-1]
    return None


def _review_notes(text: str) -> list[tuple[str, str]]:
    """PR cards store 'Review: who: text who: text'. Split them back out."""
    if "Review:" not in text:
        return []
    body = text.split("Review:", 1)[1]
    parts = re.split(r"(?:^|\s)([A-Za-z0-9][A-Za-z0-9\-\[\]]{1,38}):\s", body)
    return [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def _cards(project: str, idx: str) -> list[dict]:
    return [{"id": r["id"], "text": r["text"], "metadata": json.loads(r["meta"])}
            for r in db.q("SELECT id, text, meta FROM cards WHERE project=? AND idx=?", (project, idx))]


def _index(project: str, eid: str):
    from .context import save_cards  # persisted as cards so memory warms up after a restart
    db.x("DELETE FROM cards WHERE project=? AND idx='experts' AND meta LIKE ?", (project, f'%"{eid}"%'))
    rows = db.q("SELECT * FROM lessons WHERE expert=?", (eid,))
    save_cards(project, "experts", [{"id": r["id"], "text": r["text"],
                                     "metadata": {"type": "lesson", "expert": eid, "source": r["source"], "by": r["taught_by"], "ref": r["ref"] or ""}}
                                    for r in rows])


def teach(project: str, eid: str, text: str, by: str, source: str = "manual", ref: str = ""):
    lid = db.uid("les")
    db.x("INSERT INTO lessons VALUES(?,?,?,?,?,?,?,?)", (lid, project, eid, text.strip(), by, source, ref, time.time()))
    from .context import save_cards
    save_cards(project, "experts", [{"id": lid, "text": text, "metadata": {"type": "lesson", "expert": eid, "source": source, "by": by, "ref": ref}}])
    db.event(project, None, by, "expert_taught", {"expert": eid, "note": text[:80]})
    return lid


# ---------------- who is senior here ----------------

def experience(project: str, user_id: str, area) -> int:
    """PRs a person wrote or reviewed in an area (a path prefix or a list of files). Titles lie; history doesn't."""
    u = db.one("SELECT github FROM users WHERE id=?", (user_id,))
    if not u or not u["github"]:
        return 0
    paths = area if isinstance(area, list) else [area]
    n = 0
    for e in graph.neighbors(project, f"person:{u['github']}", {"authored", "reviewed"}, "out"):
        files = [r["path"] for r in db.q("SELECT path FROM pr_files WHERE project=? AND pr=?", (project, e["id"]))]
        if any(f.startswith(p) for f in files for p in paths):
            n += 1
    return n


def is_senior(project: str, user_id: str | None, area) -> bool:
    if not user_id:
        return False
    m = db.one("SELECT level FROM members WHERE project=? AND user=?", (project, user_id))
    if m and m["level"] in ("senior", "staff"):
        return True
    if m and m["level"] in ("junior", "mid"):
        return False
    return experience(project, user_id, area) >= SENIOR_PRS


def seniors_for(project: str, eid: str) -> list[dict]:
    """People the expert learned from who are on the project, then anyone marked senior."""
    e = db.one("SELECT teachers, paths FROM experts WHERE id=?", (eid,))
    out = []
    for login, n in teachers(eid):
        u = _login_user(project, login)
        if u:
            out.append({**u, "reviews": n, "shift": (db.one("SELECT shift FROM users WHERE id=?", (u["id"],)) or {}).get("shift")})
    area = json.loads(e["paths"]) if e else []
    for m in db.q("""SELECT u.id, u.name, m.role, m.level, u.shift FROM members m JOIN users u ON u.id=m.user
                     WHERE m.project=? AND m.role IN ('engineer','lead','owner')""", (project,)):
        if not any(o["id"] == m["id"] for o in out) and is_senior(project, m["id"], area):
            out.append({**m, "reviews": 0, "prs": experience(project, m["id"], area)})
    out.sort(key=lambda o: -(o["reviews"] * 2 + o.get("prs", 0)))
    return out


# ---------------- consulting ----------------

def for_files(project: str, files: list[str]) -> dict | None:
    best, score = None, 0
    for e in db.q("SELECT * FROM experts WHERE project=?", (project,)):
        n = sum(1 for f in files for p in json.loads(e["paths"]) if f and f.startswith(p))
        if n > score:
            best, score = e, n
    return best


def consult(project: str, owner_type: str, owner_id: str, subject: str, files: list[str], owner: str | None) -> dict | None:
    """Review a plan the way the area's seniors would, with their words as evidence."""
    e = for_files(project, files)
    if not e:
        return None
    area = json.loads(e["paths"])
    lessons = recall(project, e["id"], subject + " " + " ".join(files))
    risky = risk_words(files)
    senior_owner = is_senior(project, owner, area)
    seniors = [s for s in seniors_for(project, e["id"]) if s["id"] != owner]
    signoff = None
    if risky and not senior_owner:
        on = next((s for s in seniors if s.get("shift") == "on"), None) or (seniors[0] if seniors else None)
        signoff = on["id"] if on else None

    text = _advise_model(e, subject, lessons, risky, senior_owner) or _advise_scripted(e, lessons, risky, senior_owner)
    from . import org
    team = org.recall(project, subject + " " + " ".join(files))  # how the whole team does this, from the handbook and promoted lessons
    if team:
        text += " From your team's practices: " + " ".join(f"{_first(t['text'])} ({org.TOPICS.get(t['topic'], ('team',))[0]})" for t in team)
    if risky and not senior_owner:
        who = next((s["name"] for s in seniors if s["id"] == signoff), None)
        teachers = [t for t, _ in json.loads(e["teachers"])[:2]]
        text += (f" This touches {' and '.join(risky[:3])}, so {who} should sign off before it ships."
                 if who else f" This touches risky code and no senior for {e['name'].replace(' expert', '')} is on this project. Invite {' or '.join(teachers) or 'a senior'} to review.")
    aid = db.uid("adv")
    db.x("INSERT INTO advice VALUES(?,?,?,?,?,?,?,?,?,?,?)",
         (aid, project, e["id"], owner_type, owner_id, text, json.dumps([l["id"] for l in lessons]), signoff, "pending", None, time.time()))
    return {"id": aid, "expert": e["name"], "text": text, "signoff": signoff, "lessons": lessons, "risky": risky, "senior_owner": senior_owner}


WORD = re.compile(r"[a-z][a-z0-9_]{2,}")
STOP = {"the", "and", "for", "with", "this", "that", "from", "has", "have", "are", "was", "were", "not", "but", "its", "into", "when", "then", "than", "rows", "row"}


def _words(t: str) -> set[str]:
    out = set()
    for w in WORD.findall(t.lower().replace(".", " ")):
        if w not in STOP:
            out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)  # crude stem: columns ~ column
    return out


def recall(project: str, eid: str, text: str, k: int = 4) -> list[dict]:
    """Moss search first; small knowledge bases also get a plain word-overlap pass so nothing relevant is missed."""
    hits = memory_for(project).search("experts", text, top_k=k, flt={"field": "expert", "condition": {"$eq": eid}})
    found = [l for l in (db.one("SELECT * FROM lessons WHERE id=?", (h.id,)) for h in hits) if l]
    if len(found) < k:
        q = _words(text)
        scored = sorted(((len(q & _words(l["text"])), l) for l in db.q("SELECT * FROM lessons WHERE expert=?", (eid,))), key=lambda x: -x[0])
        found += [l for n, l in scored if n >= 2 and all(f["id"] != l["id"] for f in found)][: k - len(found)]
    return found


def _advise_scripted(e: dict, lessons: list[dict], risky: list[str], senior_owner: bool) -> str:
    if not lessons:
        return f"{e['name']}: nothing in the review history matches this closely. Treat it as new ground."
    tips = []
    for l in lessons[:3]:
        src = {"review": f"{l['taught_by']} in review", "fix": "a past fix", "incident": "a past war room", "manual": f"{l['taught_by']}",
               "thread": f"{l['taught_by']}"}.get(l["source"], l["taught_by"])
        tips.append(f"{_first(l['text'])} ({src}{', ' + l['ref'].split(':')[-1] if l['ref'] and l['source'] == 'review' else ''})")
    return f"{e['name']}, from what seniors said about this area: " + " ".join(f"{i + 1}. {t}" for i, t in enumerate(tips))


def _advise_model(e: dict, subject: str, lessons: list[dict], risky: list[str], senior_owner: bool) -> str | None:
    if not lessons or not gateway.status()["online"]:
        return None
    kb = "\n".join(f"[{l['id']}] ({l['source']} by {l['taught_by']}) {l['text'][:400]}" for l in lessons)
    try:
        r = gateway.llm_call("reason", "You are a senior reviewer for one area of a codebase. Advise the engineer fixing this in 2 to 4 short sentences. "
                             "Use only the lessons given; cite each by its [id]. If none apply, say it's new ground. Never approve anything.",
                             f"Area: {e['name']}\nProblem: {subject[:600]}\nOwner is {'senior' if senior_owner else 'not senior in this area'}.\nLessons:\n{kb}\n"
                             'Reply JSON {"advice": "...", "cites": ["les_..."]}', 300)
        cites = [c for c in r.get("cites", []) if any(l["id"] == c for l in lessons)]
        return r["advice"] if r.get("advice") and cites else None  # citation gate
    except Exception:
        return None


def _first(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    m = re.match(r"(.{20,220}?[.!?])(\s|$)", t)
    return m.group(1) if m else t[:220]


def verdict(advice_id: str, verdict: str, by: str):
    db.x("UPDATE advice SET verdict=?, verdict_by=? WHERE id=?", (verdict, by, advice_id))


def settle_bug(bug_id: str):
    """A verified fix is the ground truth: advice that was followed and held counts for the expert."""
    db.x("UPDATE advice SET verdict='held' WHERE owner_type='bug' AND owner_id=? AND verdict='followed'", (bug_id,))


def teachers(eid: str, n: int = 6) -> list[tuple[str, int]]:
    """Everyone the expert learned from: reviewers, fixers, and people who taught it directly."""
    return [(r["taught_by"], r["n"]) for r in db.q("""SELECT taught_by, count(*) n FROM lessons WHERE expert=? AND taught_by NOT IN ('war room','team')
                                                     GROUP BY taught_by ORDER BY n DESC LIMIT ?""", (eid, n))]


def listing(project: str) -> list[dict]:
    out = []
    for e in db.q("SELECT * FROM experts WHERE project=? ORDER BY name", (project,)):
        src = {r["source"]: r["n"] for r in db.q("SELECT source, count(*) n FROM lessons WHERE expert=? GROUP BY source", (e["id"],))}
        adv = {r["verdict"]: r["n"] for r in db.q("SELECT verdict, count(*) n FROM advice WHERE expert=? GROUP BY verdict", (e["id"],))}
        taught = []
        for login, n in teachers(e["id"]):
            u = _login_user(e["project"], login)
            taught.append({"login": login, "n": n, "member": bool(u), "name": u["name"] if u else login})
        out.append({"id": e["id"], "name": e["name"], "paths": json.loads(e["paths"]), "built": e["built"], "lessons": src,
                    "teachers": taught, "track": adv,
                    "recent": db.q("SELECT id, text, taught_by, source, ref FROM lessons WHERE expert=? ORDER BY (source='manual' OR source='thread') DESC, at DESC LIMIT 4", (e["id"],))})
    return out
