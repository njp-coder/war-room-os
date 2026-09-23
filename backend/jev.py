"""Jev (TypeSafe System One) for the calls that need a calibrated probability, not prose.

Two decisions use it:
  1. Is this production signal a real incident?  -> monitoring holds, proposes or opens a war room
  2. Which change caused this problem?           -> each candidate PR gets its own probability; "how sure" is Jev's number

Every prediction is stored with its subject. When a person later settles the question (opens or dismisses a war room,
resolves it), the outcome is stored next to it, so calibration on this team's own history can be checked (`calibration`).
No key, an error or a timeout returns None and callers keep their rule-based path.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time

import httpx

from . import db, gateway

CFG = {"endpoint": "https://openrouter.ai/api/alpha/decisions", "model": "~typesafe/jev-latest",
       "api_key_env": "OPENROUTER_API_KEY", "timeout_s": 20, **(gateway.CONFIG.get("jev") or {})}

db.x("""CREATE TABLE IF NOT EXISTS jev_decisions(id TEXT PRIMARY KEY, project TEXT, subject_type TEXT, subject_id TEXT, purpose TEXT,
        question TEXT, instructions TEXT, p REAL, model TEXT, at REAL, outcome INTEGER, settled_at REAL)""")
db.x("CREATE INDEX IF NOT EXISTS jev_subject ON jev_decisions(subject_type, subject_id)")

meter = {"calls": 0, "cached": 0, "errors": 0, "cost_usd": 0.0, "input_tokens": 0}
_lock = threading.Lock()


def _key() -> str | None:
    return (os.getenv(CFG["api_key_env"]) or "").strip() or None


def status() -> dict:
    return {"online": bool(_key()), "model": CFG["model"], **meter}


def decide(project: str, subject_type: str, subject_id: str, purpose: str, state: str,
           questions: dict[str, str]) -> dict[str, float] | None:
    """Ask Jev yes/no questions about `state`. Returns {question_id: probability of yes}, or None if Jev is unavailable."""
    key = _key()
    if not key or not questions:
        return None
    state = state[:12000]
    ck = hashlib.sha256(json.dumps([CFG["model"], state, questions], sort_keys=True).encode()).hexdigest()
    with _lock:
        row = gateway.CACHE.execute("SELECT response FROM cache WHERE key=?", ("jev:" + ck,)).fetchone()
    model = CFG["model"]
    if row:
        meter["cached"] += 1
        probs = json.loads(row[0])
    else:
        try:
            r = httpx.post(CFG["endpoint"], timeout=CFG["timeout_s"],
                           headers={"Authorization": f"Bearer {key}", "X-OpenRouter-Title": "War Room OS"},
                           json={"model": CFG["model"], "state": state,
                                 "questions": {q: {"type": "noul", "instructions": text} for q, text in questions.items()}})
            r.raise_for_status()
            data = r.json()
            answers = data.get("answers") or {}
            probs = {q: float(answers[q]["noul"]) for q in questions if isinstance((answers.get(q) or {}).get("noul"), (int, float))}
            if len(probs) != len(questions):
                raise ValueError(f"missing answers for {sorted(set(questions) - set(probs))}")
            model = data.get("model", model)
            u = data.get("usage") or {}
            meter["calls"] += 1
            meter["cost_usd"] += float(u.get("cost") or 0)
            meter["input_tokens"] += int(u.get("input_tokens") or 0)
            with _lock:
                gateway.CACHE.execute("INSERT OR REPLACE INTO cache VALUES(?,?)", ("jev:" + ck, json.dumps(probs)))
                gateway.CACHE.commit()
        except Exception as e:
            meter["errors"] += 1
            print(f"[jev] {purpose} for {subject_type}:{subject_id} failed, using rules: {type(e).__name__} {str(e)[:200]}", flush=True)
            return None
    now = time.time()
    db.xmany("INSERT INTO jev_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
             [(db.uid("jd"), project, subject_type, subject_id, purpose, q, questions[q], p, model, now, None, None) for q, p in probs.items()])
    return probs


def settle(subject_type: str, subject_id: str, question: str, outcome: bool):
    """Record what actually happened for the latest prediction on this subject and question."""
    row = db.one("SELECT id FROM jev_decisions WHERE subject_type=? AND subject_id=? AND question=? ORDER BY at DESC",
                 (subject_type, subject_id, question))
    if row:
        db.x("UPDATE jev_decisions SET outcome=?, settled_at=? WHERE id=?", (int(outcome), time.time(), row["id"]))


def calibration(project: str) -> dict:
    """Settled predictions bucketed by probability: a calibrated model's 80% bucket comes true about 80% of the time."""
    rows = db.q("SELECT purpose, p, outcome FROM jev_decisions WHERE project=? AND outcome IS NOT NULL", (project,))
    buckets = []
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        inb = [r for r in rows if lo <= r["p"] < lo + 0.2 or (lo == 0.8 and r["p"] == 1.0)]
        if inb:
            buckets.append({"range": f"{int(lo * 100)}-{int(lo * 100) + 20}%", "n": len(inb),
                            "predicted": round(sum(r["p"] for r in inb) / len(inb), 2),
                            "happened": round(sum(r["outcome"] for r in inb) / len(inb), 2)})
    brier = round(sum((r["p"] - r["outcome"]) ** 2 for r in rows) / len(rows), 3) if rows else None
    total = db.one("SELECT count(*) n FROM jev_decisions WHERE project=?", (project,))["n"]
    return {"settled": len(rows), "predictions": total, "brier": brier, "buckets": buckets}


# ---------------- the two decisions ----------------

REAL_Q = {
    "real": "Does this need an engineer's attention now, rather than being noise, a one-off blip, or expected behaviour?",
    "impact": "Are customers likely noticing it right now, as failed or noticeably slow requests?",
}


def incident_odds(project: str, breach: dict) -> dict[str, float] | None:
    """Is a monitoring signal a real incident? State: the signal, its baseline, recent changes and what people said last time."""
    from . import graph
    lines = [f"Production signal from the monitoring agent. Rule: {breach['rule']}. Severity by rule: {breach['severity']}.",
             f"Summary: {breach['title']}."]
    if breach.get("value") is not None:
        lines.append(f"Current value: {breach['value']}" + (f", baseline {breach['baseline']}" if breach.get("baseline") is not None else ", no baseline yet")
                     + (f", over {breach['calls']} calls in the last check" if breach.get("calls") else "") + ".")
    if breach.get("query"):
        lines.append(f"Query: {breach['query'][:600]}")
    if breach.get("template"):
        lines.append(f"Error: {breach['template'][:300]}")
    if breach.get("sample"):
        lines.append(f"Sample log line: {str(breach['sample'])[:600]}")
    # what changed recently in the code this signal touches
    prs = []
    try:
        if breach.get("frames"):
            from .sources import frames_to_code
            prs = frames_to_code(project, breach["frames"]).get("prs", [])
        elif breach.get("query"):
            from .monitor import query_context
            ctx = query_context(project, breach["query"])
            prs = ctx.get("prs", [])
            if ctx.get("hints"):
                lines.append("Index check: " + "; ".join(ctx["hints"]))
    except Exception:
        pass
    for p in prs[:3]:
        n = graph.node(project, p) or {"label": p, "props": {}}
        lines.append(f"Code involved was last changed by {n['label']}" + (f", merged {n['props'].get('merged_at')}" if n["props"].get("merged_at") else "") + ".")
    history = db.q("SELECT status, dismissed_reason FROM incidents WHERE project=? AND fingerprint=? ORDER BY opened_at DESC LIMIT 5",
                   (project, breach["fingerprint"])) if _has_col("incidents", "dismissed_reason") else \
        db.q("SELECT status FROM incidents WHERE project=? AND fingerprint=? ORDER BY opened_at DESC LIMIT 5", (project, breach["fingerprint"]))
    if history:
        lines.append("Earlier war rooms for this same signal: " + ", ".join(h["status"] for h in history) + ".")
    else:
        lines.append("This signal has never raised a war room before.")
    lines.append(f"Local time at the service: {time.strftime('%A %H:%M')}.")
    from .brief import summary
    b = summary(project)
    if b:
        lines.append("What the team told us about this product:\n" + b)
    return decide(project, "signal", breach["fingerprint"], "incident_odds", "\n".join(lines), REAL_Q)


def rank_causes(project: str, subject_type: str, subject_id: str, problem: str, candidates: list[str],
                in_release: set[str] | None = None) -> tuple[list[tuple[str, float]], float | None] | None:
    """Probability that each candidate change caused the problem, plus the probability it's none of them."""
    from . import graph
    if not candidates:
        return None
    in_release = in_release or set()
    from .brief import summary
    from .tracks import summary as team_summary
    b = summary(project, 600)
    owner = db.one(f"SELECT track FROM {'bugs' if subject_type == 'bug' else 'incidents'} WHERE id=?", (subject_id,)) if subject_type in ("bug", "incident") else None
    team = team_summary(project, owner["track"], 400) if owner and owner.get("track") else ""
    lines = [f"Problem: {problem[:1500]}"] + ([f"About the product: {b}"] if b else []) + ([team] if team else []) + ["", "Candidate changes:"]
    for i, pid in enumerate(candidates):
        n = graph.node(project, pid) or {"label": pid, "props": {}}
        card = db.one("SELECT text FROM cards WHERE project=? AND id=?", (project, pid))
        files = [r["path"] for r in db.q("SELECT path FROM pr_files WHERE project=? AND pr=?", (project, pid))][:12]
        lines.append(f"[{i + 1}] {n['label']}" + (" (ships in the release under test)" if pid in in_release else "")
                     + (f". Merged {n['props'].get('merged_at')}" if n["props"].get("merged_at") else ". Not merged yet"))
        if card:
            lines.append(f"    {card['text'][:700]}")
        if files:
            lines.append(f"    Files: {', '.join(files)}")
    qs = {f"c{i}": f"Did change [{i + 1}] ({(graph.node(project, pid) or {'label': pid})['label'][:80]}) cause this problem?" for i, pid in enumerate(candidates)}
    qs["none"] = "Is the cause most likely something other than all of these changes (data, configuration, infrastructure or older code)?"
    probs = decide(project, subject_type, subject_id, "rank_causes", "\n".join(lines), qs)
    if probs is None:
        return None
    ranked = sorted(((pid, probs[f"c{i}"]) for i, pid in enumerate(candidates)), key=lambda x: -x[1])
    return ranked, probs.get("none")


def _has_col(table: str, col: str) -> bool:
    return any(r["name"] == col for r in db.q(f"PRAGMA table_info({table})"))
