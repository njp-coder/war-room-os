"""The project brief: what the code can't tell agents.

Code gives structure. It doesn't say what the product is for, which flows matter, when peak is, what counts as broken,
which rules live in people's heads, what's sensitive, how changes ship, or who to call. A short set of questions covers
that: eight fixed ones, plus a few generated from what the repo sync found (areas one person knows, tables written from
many places). Answers become context cards agents search, and a compact summary goes into Jev's questions.
"""
from __future__ import annotations

import json
import time

from . import db

db.x("""CREATE TABLE IF NOT EXISTS brief(project TEXT, qid TEXT, kind TEXT, question TEXT, hint TEXT, choices TEXT DEFAULT '[]',
        answer TEXT, answered_by TEXT, answered_at REAL, created REAL, PRIMARY KEY(project, qid))""")

CORE = [
    ("what", "In two or three sentences, what does this product do and who uses it?",
     "e.g. Small shops take orders online; their customers pay by UPI and track delivery."),
    ("critical", "Which flows must never break?", "e.g. Checkout, login, and the daily payout run."),
    ("peak", "When is traffic highest, and what does normal look like then?", "e.g. 12:00 to 14:30 IST, about 40 requests a second."),
    ("broken", "What counts as an incident for you?", "e.g. Checkout errors above 1%, or any page slower than 2 s at peak. A slow admin report isn't."),
    ("rules", "Which business rules aren't obvious from the code?", "e.g. Refunds over ₹5,000 need a manager. Discounts apply before tax."),
    ("sensitive", "What data is sensitive?", "e.g. Phone numbers and addresses: never in logs, screenshots or bug reports."),
    ("deploys", "How do changes reach production, and how do you roll back?", "e.g. Merging to main deploys at 16:00. Roll back by redeploying the previous tag."),
    ("people", "Who should be called for what, and who talks to the client?", "e.g. Payments issues go to the payments lead; the account lead tells the client."),
]


def ensure(project: str) -> None:
    now = time.time()
    for qid, q, hint in CORE:
        db.x("INSERT OR IGNORE INTO brief(project, qid, kind, question, hint, created) VALUES(?,?,?,?,?,?)", (project, qid, "core", q, hint, now))
    _generated(project)


def _generated(project: str) -> None:
    """Questions only this project's code can prompt. Regenerated after each sync; answered ones are kept."""
    from . import areas
    try:
        a = areas.build(project)
    except Exception:
        return
    qs = []
    eps = list(dict.fromkeys(e for x in a["areas"] for e in x["endpoints"] if " /" in e or e.count(" ") == 0))
    if eps:
        qs.append(("customer_facing", "Which of these endpoints do customers hit directly?", "Pick the ones a customer's app or browser calls.", eps[:24]))
    for x in a["areas"]:
        if x["single_owner"] and x["prs"] and x["people"]:
            who = x["people"][0]["name"]
            qs.append((f"owner_{x['id']}", f"Only {who} has changed {x['name']}. Who else can approve a fix there?",
                       "A name, or 'nobody yet'.", []))
    writers: dict[str, list[str]] = {}
    for x in a["areas"]:
        for t in x["writes"]:
            writers.setdefault(t, []).append(x["name"])
    for t, names in writers.items():
        if len(names) >= 3:
            qs.append((f"table_{t}", f"{t} is written from {len(names)} areas ({', '.join(names[:4])}). Is one of them the source of truth?",
                       "Which area owns it, and what must the others never change?", []))
    now = time.time()
    keep = {q[0] for q in qs}
    for qid, q, hint, choices in qs[:8]:
        db.x("""INSERT INTO brief(project, qid, kind, question, hint, choices, created) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(project, qid) DO UPDATE SET question=excluded.question, choices=excluded.choices""",
             (project, qid, "generated", q, hint, json.dumps(choices), now))
    for r in db.q("SELECT qid FROM brief WHERE project=? AND kind='generated' AND answer IS NULL", (project,)):
        if r["qid"] not in keep:
            db.x("DELETE FROM brief WHERE project=? AND qid=?", (project, r["qid"]))


def listing(project: str) -> list[dict]:
    ensure(project)
    rows = db.q("SELECT * FROM brief WHERE project=? AND kind<>'track' ORDER BY kind='generated', created, qid", (project,))
    order = [q[0] for q in CORE]
    rows.sort(key=lambda r: (r["kind"] == "generated", order.index(r["qid"]) if r["qid"] in order else 99))
    for r in rows:
        r["choices"] = json.loads(r["choices"] or "[]")
    return rows


def answer(project: str, qid: str, text: str, by: str) -> None:
    from .context import save_cards
    row = db.one("SELECT question FROM brief WHERE project=? AND qid=?", (project, qid))
    if not row:
        raise KeyError(qid)
    text = text.strip()
    db.x("UPDATE brief SET answer=?, answered_by=?, answered_at=? WHERE project=? AND qid=?", (text or None, by, time.time(), project, qid))
    if text:
        save_cards(project, "knowledge", [{"id": f"brief:{qid}", "text": f"{row['question']} {text}",
                                           "metadata": {"type": "brief", "question": qid, "by": by}}])


def summary(project: str, limit: int = 1200) -> str:
    """The answers that change how agents judge things, as a few lines for model and Jev prompts."""
    rows = {r["qid"]: r for r in db.q("SELECT qid, question, answer FROM brief WHERE project=? AND answer IS NOT NULL", (project,))}
    lines = []
    for qid, label in [("what", "Product"), ("critical", "Must never break"), ("peak", "Peak"), ("broken", "Counts as an incident"),
                       ("rules", "Business rules"), ("sensitive", "Sensitive data")]:
        if qid in rows:
            lines.append(f"{label}: {rows[qid]['answer']}")
    if "customer_facing" in rows:
        lines.append(f"Customer-facing endpoints: {rows['customer_facing']['answer']}")
    return "\n".join(lines)[:limit]


def progress(project: str) -> tuple[int, int]:
    ensure(project)
    r = db.one("SELECT count(*) n, sum(answer IS NOT NULL) a FROM brief WHERE project=? AND kind<>'track'", (project,))
    return int(r["a"] or 0), int(r["n"] or 0)
