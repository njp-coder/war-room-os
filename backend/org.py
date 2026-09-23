"""Org experts: how the whole team does things, shared by every project in the org.

Project experts know one codebase. Org experts know a technology or domain (Postgres, FastAPI, payments...) the way the
team practises it, so a brand-new project gets them on day one. They learn from:
  - the team's handbook: standards, runbooks, past postmortems, uploaded once (Markdown, text or PDF)
  - promoted lessons: a project lesson that proved itself, rewritten in general terms and approved by an admin or lead
A project gets the org experts that match its stack, detected from its dependency files and connected databases.

For agencies: projects belong to different clients. Nothing moves from a project to the org automatically. Promotion
rewrites the lesson without client names, data or identifiers, and a person approves the result before anyone else sees it.
"""
from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path

from . import db, gateway

db.x("""CREATE TABLE IF NOT EXISTS org_lessons(id TEXT PRIMARY KEY, org TEXT, topic TEXT, text TEXT, source TEXT, origin TEXT,
        status TEXT, proposed_by TEXT, approved_by TEXT, doc TEXT, at REAL)""")
db.x("CREATE TABLE IF NOT EXISTS org_docs(id TEXT PRIMARY KEY, org TEXT, name TEXT, sections INT, by TEXT, at REAL)")

# topic -> (label, dependency or signal patterns)
TOPICS = {
    "postgres": ("Postgres", [r"psycopg", r"asyncpg", r"\bpg\b", r"postgres", r"pg_", r"alembic"]),
    "mysql": ("MySQL", [r"pymysql", r"mysqlclient", r"\bmysql2?\b"]),
    "mongodb": ("MongoDB", [r"pymongo", r"motor", r"mongoose", r"mongodb"]),
    "dynamodb": ("DynamoDB", [r"boto3", r"dynamodb", r"@aws-sdk/client-dynamodb"]),
    "sqlserver": ("SQL Server", [r"pymssql", r"pyodbc", r"\bmssql\b", r"tedious"]),
    "snowflake": ("Snowflake", [r"snowflake"]),
    "bigquery": ("BigQuery", [r"bigquery"]),
    "redis": ("Redis", [r"\bredis\b", r"ioredis"]),
    "fastapi": ("FastAPI", [r"fastapi"]),
    "django": ("Django", [r"\bdjango\b"]),
    "flask": ("Flask", [r"\bflask\b"]),
    "sqlalchemy": ("SQLAlchemy and SQLModel", [r"sqlalchemy", r"sqlmodel"]),
    "express": ("Express", [r"\bexpress\b"]),
    "nextjs": ("Next.js", [r"\bnext\b"]),
    "react": ("React", [r"\breact\b"]),
    "prisma": ("Prisma", [r"prisma"]),
    "celery": ("Background jobs", [r"celery", r"\brq\b", r"bullmq", r"sidekiq"]),
    "payments": ("Payments", [r"stripe", r"razorpay", r"paypal", r"braintree", r"\bpayment", r"billing", r"coupon", r"invoice"]),
    "auth": ("Auth and security", [r"jwt", r"oauth", r"passlib", r"bcrypt", r"next-auth", r"authlib", r"\bauth\b"]),
    "migrations": ("Database migrations", [r"alembic", r"migrations?", r"flyway", r"liquibase", r"knex"]),
}
MANIFESTS = ["requirements.txt", "pyproject.toml", "Pipfile", "setup.py", "package.json", "go.mod", "Gemfile", "pom.xml", "build.gradle"]


# ---------------- which org experts apply to a project ----------------

def project_topics(project: str) -> list[str]:
    """From dependency files in the repo, the connected databases, and what the code's areas are called."""
    from .experts import _repo_root
    text = ""
    root = _repo_root(project)
    if root:
        for m in MANIFESTS:
            for p in list(root.glob(m)) + list(root.glob(f"*/{m}"))[:6]:
                if p.stat().st_size < 300_000:
                    text += "\n" + p.read_text(errors="ignore").lower()
        if list(root.glob("**/alembic/versions"))[:1] or list(root.glob("**/migrations"))[:1]:
            text += "\nmigrations alembic"
    from . import staging
    for kind in ("staging_db", "monitor_db"):
        try:
            if staging.connected(project, kind):
                text += "\n" + staging.dialect(project, kind)
        except Exception:
            pass
    text += "\n" + " ".join(r["label"].lower() for r in db.q("SELECT label FROM nodes WHERE project=? AND type IN ('table','endpoint')", (project,)))
    text = text.replace("tsql", "mssql")
    return [t for t, (_, pats) in TOPICS.items() if any(re.search(p, text) for p in pats)]


def experts_for(project: str) -> list[dict]:
    org = (db.one("SELECT org FROM projects WHERE id=?", (project,)) or {}).get("org")
    topics = project_topics(project)
    out = []
    for t in topics:
        n = db.one("SELECT count(*) n FROM org_lessons WHERE org=? AND topic=? AND status='approved'", (org, t))["n"]
        out.append({"topic": t, "name": f"{TOPICS[t][0]} expert", "lessons": n})
    return out


# ---------------- handbook ----------------

def _sections(name: str, text: str) -> list[tuple[str, str]]:
    """Split a handbook into sections by headings (Markdown) or paragraphs, each small enough to cite."""
    text = text.replace("\r\n", "\n")
    parts = re.split(r"\n(?=#{1,4} )", "\n" + text)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        title = p.split("\n", 1)[0].lstrip("#").strip() if p.startswith("#") else name
        body = p.split("\n", 1)[1].strip() if p.startswith("#") and "\n" in p else p
        for chunk in re.split(r"\n{2,}", body) if len(body) > 1500 else [body]:
            if len(chunk.strip()) >= 40:
                out.append((title[:120], chunk.strip()[:1500]))
    return out


def _tag(title: str, body: str) -> list[str]:
    low = f"{title} {body}".lower()
    tags = [t for t, (label, pats) in TOPICS.items() if label.lower() in low or any(re.search(p, low) for p in pats)]
    return tags or ["general"]


def read_upload(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        from pypdf import PdfReader
        return "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    return data.decode("utf-8", errors="ignore")


def add_handbook(org: str, filename: str, data: bytes, by: str) -> dict:
    text = read_upload(filename, data)
    name = Path(filename).stem.replace("_", " ").replace("-", " ")
    did = db.uid("doc")
    secs = _sections(name, text)
    rows = []
    for title, body in secs:
        for topic in _tag(title, body):
            rows.append((db.uid("olz"), org, topic, f"{title}: {body}", "handbook", filename, "approved", by, by, did, time.time()))
    db.xmany("INSERT INTO org_lessons VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.x("INSERT INTO org_docs VALUES(?,?,?,?,?,?)", (did, org, filename, len(secs), by, time.time()))
    _index(org)
    return {"doc": did, "sections": len(secs), "lessons": len(rows), "topics": sorted({r[2] for r in rows})}


def remove_handbook(org: str, did: str):
    db.x("DELETE FROM org_lessons WHERE org=? AND doc=?", (org, did))
    db.x("DELETE FROM org_docs WHERE org=? AND id=?", (org, did))
    _index(org)


# ---------------- promotion from a project ----------------

def propose(org: str, lesson_id: str, topic: str, by: str) -> dict:
    """Rewrite a project lesson as a general rule with no client specifics, then wait for approval."""
    les = db.one("SELECT * FROM lessons WHERE id=?", (lesson_id,))
    if not les:
        raise KeyError(lesson_id)
    general = None
    try:
        r = gateway.llm_call("fast", "You turn one project's engineering lesson into a general rule for a whole engineering team. "
                             "Remove client names, product names, people, table and column names, URLs and numbers that identify the project. "
                             "Keep the technique and the reason. One or two plain sentences. If nothing general is left, reply with an empty rule.",
                             f"Topic: {TOPICS.get(topic, (topic,))[0]}\nLesson: {les['text'][:800]}\n" + 'Reply JSON {"rule": "..."}', 160)
        general = (r.get("rule") or "").strip() if isinstance(r, dict) else None
    except Exception:
        pass
    if not general:
        general = les["text"][:600]  # model offline: the approver must edit it before approving
    oid = db.uid("olz")
    db.x("INSERT INTO org_lessons VALUES(?,?,?,?,?,?,?,?,?,?,?)",
         (oid, org, topic, general, "promoted", les["project"], "pending", by, None, None, time.time()))
    return db.one("SELECT * FROM org_lessons WHERE id=?", (oid,))


def decide(org: str, oid: str, approve: bool, by: str, text: str | None = None):
    if approve:
        db.x("UPDATE org_lessons SET status='approved', approved_by=?, text=coalesce(?, text), origin=NULL WHERE id=? AND org=?",
             (by, (text or "").strip() or None, oid, org))  # origin dropped: the approved rule no longer points at a client's project
    else:
        db.x("DELETE FROM org_lessons WHERE id=? AND org=? AND status='pending'", (oid, org))
    _index(org)


def teach(org: str, topic: str, text: str, by: str) -> str:
    oid = db.uid("olz")
    db.x("INSERT INTO org_lessons VALUES(?,?,?,?,?,?,?,?,?,?,?)", (oid, org, topic, text.strip(), "manual", None, "approved", by, by, None, time.time()))
    _index(org)
    return oid


# ---------------- recall ----------------

def _index(org: str):
    """Approved org lessons live in their own memory namespace, never inside a client project's."""
    from .memory import memory_for
    rows = db.q("SELECT id, topic, text, source FROM org_lessons WHERE org=? AND status='approved'", (org,))
    if rows:
        memory_for(f"org-{org}").add("experts", [{"id": r["id"], "text": r["text"], "metadata": {"type": "org_lesson", "topic": r["topic"], "source": r["source"]}}
                                                 for r in rows])


def recall(project: str, text: str, k: int = 3) -> list[dict]:
    """The org lessons most relevant to a problem, from topics this project's stack uses."""
    from .experts import _words
    org = (db.one("SELECT org FROM projects WHERE id=?", (project,)) or {}).get("org")
    topics = set(project_topics(project)) | {"general"}
    rows = [r for r in db.q("SELECT * FROM org_lessons WHERE org=? AND status='approved'", (org,)) if r["topic"] in topics]
    if not rows:
        return []
    q = _words(text)
    scored = sorted(((len(q & _words(r["text"])), r) for r in rows), key=lambda x: -x[0])
    return [r for n, r in scored if n >= 2][:k]


def listing(org: str) -> dict:
    lessons = db.q("SELECT * FROM org_lessons WHERE org=? ORDER BY at DESC", (org,))
    by_topic: dict[str, dict] = {}
    for l in lessons:
        if l["status"] != "approved":
            continue
        t = by_topic.setdefault(l["topic"], {"topic": l["topic"], "name": f"{TOPICS.get(l['topic'], ('General',))[0]} expert", "count": 0, "sources": {}, "recent": []})
        t["count"] += 1
        t["sources"][l["source"]] = t["sources"].get(l["source"], 0) + 1
        if len(t["recent"]) < 4:
            t["recent"].append({"id": l["id"], "text": l["text"][:240], "source": l["source"]})
    projects = []
    for p in db.q("SELECT id, name FROM projects WHERE org=? AND demo=0", (org,)):
        projects.append({"id": p["id"], "name": p["name"], "topics": project_topics(p["id"])})
    return {"experts": sorted(by_topic.values(), key=lambda t: -t["count"]), "pending": [l for l in lessons if l["status"] == "pending"],
            "docs": db.q("SELECT * FROM org_docs WHERE org=? ORDER BY at DESC", (org,)), "projects": projects,
            "topics": [{"id": k, "label": v[0]} for k, v in TOPICS.items()]}
