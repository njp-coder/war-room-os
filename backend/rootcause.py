"""Root-cause agent, harness style.

1. Pre-flight evidence packet (code only): symptom, who's affected, what changed,
   where in the code, seen before, ruled out. Each item is a card with an ID.
2. Harness loop: the model sees the packet as IDs + one-liners and may call
   expand_card / moss_search, then returns theories. Budgeted in turns.
3. Hooks: citation gate (every cite must be a real card in the packet or memory),
   ruled-out filter, run_check always needs human approval.
4. Offline path: if no model key, theories come from matched playbooks + evidence.
"""
from __future__ import annotations

import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

from . import gateway
from .memory import memory, f_eq
from .store import store, Theory, Approval, clock_label

REPLICA = Path(__file__).parent / "demo" / "data" / "legacy.db"
ATTRS = [("batch", lambda u: u["batch"]), ("account", lambda u: u["account_type"]),
         ("signup", lambda u: "before 2019" if u["signup_year"] < 2019 else "2019 or later"),
         ("platform", lambda u: u["platform"]), ("timezone", lambda u: u["tz"]), ("plan", lambda u: u["plan"])]


def _fields(meta: dict) -> set[str]:
    return {f for f in meta.get("fields", "").split(",") if f}


def cohort_stats(cluster, world) -> list[dict]:
    affected_ids = set(cluster.user_ids)
    affected = [world.users[u] for u in affected_ids]
    others = [u for uid, u in world.users.items() if uid not in affected_ids]
    rows = []
    for name, fn in ATTRS:
        a = Counter(fn(u) for u in affected)
        o = Counter(fn(u) for u in others)
        for value, n in a.items():
            share_a = n / len(affected)
            share_o = o.get(value, 0) / max(len(others), 1)
            if share_a >= 0.6 and share_a >= 1.5 * share_o:
                rows.append({"attr": f"{name} = {value}", "affected": round(share_a * 100), "others": round(share_o * 100)})
    rows.sort(key=lambda r: -(r["affected"] - r["others"]))
    return rows[:3]


def preflight(cluster, world) -> dict:
    t0 = time.perf_counter()
    texts = " ".join(store.tickets[t]["text"] for t in cluster.ticket_ids[-20:])
    packet = {"steps": [], "cards": {}}

    def step(name, source, summary, cards):
        for c in cards:
            packet["cards"][c["id"]] = c
        packet["steps"].append({"name": name, "source": source, "summary": summary, "cites": [c["id"] for c in cards]})

    # symptom: the nearest playbook gives the likely fields; logs must agree on a field
    skills = [h for h in memory.search("knowledge", texts, top_k=3, flt=f_eq("type", "skill")) if h.score > 0.05]
    if len(skills) > 1:
        skills = [skills[0]] + [h for h in skills[1:] if h.score >= 0.5 * skills[0].score]
    fields = set()
    for h in skills[:1]:
        fields |= _fields(h.metadata)
    logs = [h for h in memory.search("signals", texts + " " + " ".join(fields), top_k=6, flt=f_eq("type", "log"))
            if not fields or _fields(h.metadata) & fields]
    if not fields and logs:
        fields |= _fields(logs[0].metadata)
    if not logs and fields:  # entity join: any log pattern tagged with these fields
        logs = [_as_hit(c) for c in memory.cards.values() if c["metadata"].get("type") == "log" and _fields(c["metadata"]) & fields]
    cluster.fields = sorted(fields)
    top_log = logs[0] if logs else None
    if top_log:
        cluster.service = top_log.metadata.get("service", "")
    step("Symptom", "signals", f"{len(cluster.ticket_ids)} tickets. " + (f"Log pattern {top_log.id} matches ({int(top_log.metadata.get('count', 0)):,} hits), first seen {clock_label(int(top_log.metadata.get('minute', 0)))}." if top_log else "No matching log pattern."),
         [{"id": h.id, "text": h.text} for h in logs[:1]])

    # who's affected (plain statistics, no model)
    rows = cohort_stats(cluster, world)
    packet["cohort"] = rows
    step("Who's affected", "cohort stats, no model",
         "; ".join(f"{r['attr']}: {r['affected']}% of affected vs {r['others']}% of others" for r in rows) or "No attribute stands out.", [])

    # what changed shortly before first report (or anything touching these fields)
    changes = []
    for h in memory.search("changes", texts + " " + " ".join(fields), top_k=12):
        minute = int(h.metadata.get("minute", "0"))
        touches = bool(_fields(h.metadata) & fields) or any(b in h.text for r in rows for b in re.findall(r"batch-\d", r["attr"]))
        if minute <= cluster.first_seen and (touches or cluster.first_seen - minute <= 360):
            changes.append((touches, minute, h))
    changes.sort(key=lambda x: (not x[0], x[2].metadata.get("type") != "commit", -x[1]))
    chosen = [h for _, _, h in changes[:3]]
    packet["change"] = next((h for h in chosen if h.metadata.get("type") == "commit"), chosen[0] if chosen else None)
    step("What changed", "changes", "; ".join(h.text.split(". Review:")[0] for h in chosen) or "Nothing in the window.",
         [{"id": h.id, "text": h.text} for h in chosen])

    # where in the code
    files = set()
    for h in chosen:
        files |= {f for f in h.metadata.get("files", "").split(",") if f}
    code = [memory.get(f"code:{f}") for f in files if memory.get(f"code:{f}")]
    step("Where in the code", "knowledge", ", ".join(c["metadata"]["file"] for c in code) or "No file found.",
         [{"id": c["id"], "text": c["text"]} for c in code])

    # seen before: risk map, playbooks, incidents, earlier fixes
    seen = [h for h in memory.search("knowledge", texts + " " + " ".join(fields), top_k=8)
            if (h.metadata.get("type") in ("risk", "skill") and _fields(h.metadata) & fields)
            or (h.metadata.get("type") == "incident" and h.score > 0.15)]
    seen_ids = {h.id for h in seen}
    seen = [_as_hit(c) for c in memory.cards.values() if c["metadata"].get("type") == "risk"
            and _fields(c["metadata"]) & fields and c["id"] not in seen_ids][:1] + seen
    fixes = [h for h in memory.search("changes", "fix hotfix " + " ".join(fields), top_k=5)
             if "fix" in h.text.lower() and _fields(h.metadata) & fields and int(h.metadata.get("minute", 0)) < cluster.first_seen]

    def label(h):
        if h.metadata.get("type") == "skill":
            sk = world_skill(world, h.id)
            return f"Playbook: {sk['name']}" if sk else h.id
        if h.metadata.get("type") == "risk":
            return "Risk map, predicted before go-live: " + h.text.split(": ", 1)[1].split(". ")[0]
        return h.text.split(". ")[0]
    step("Seen before", "knowledge + changes", "; ".join(label(h) for h in (seen[:3] + fixes[:1])) or "Nothing similar.",
         [{"id": h.id, "text": label(h)} for h in seen[:3] + fixes[:1]])

    # ruled out by humans
    ruled = [t for t in cluster.theories if t.status == "ruled_out"]
    human = memory.search("human", texts, top_k=5, flt=f_eq("cluster", cluster.id))
    step("Ruled out", "human", "; ".join(f"{t.text} ({t.ruled_out_by})" for t in ruled) or "Nothing yet.",
         [{"id": h.id, "text": h.text} for h in human if h.metadata.get("type") == "ruled_out"])

    packet["skills"] = [world_skill(world, h.id) for h in skills if world_skill(world, h.id)]
    packet["fixes"] = fixes
    packet["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return packet


def _as_hit(card: dict):
    from .memory import Hit
    return Hit(card["id"], card["text"], card["metadata"], 1.0, card["metadata"].get("index", ""))


def world_skill(world, card_id: str):
    name = card_id.split(":", 1)[1]
    return next((s for s in world.skills if s["file"] == name), None)


# ---------------- theories ----------------

def offline_theories(cluster, packet) -> list[Theory]:
    out = []
    ruled = {t.text for t in cluster.theories if t.status == "ruled_out"}
    change = packet.get("change")
    for i, sk in enumerate(packet["skills"]):
        cites = [c for s in packet["steps"] for c in s["cites"]]
        conf = 35
        if packet["cohort"]:
            conf += 20
        if change is not None and _fields(change.metadata) & set(cluster.fields):
            conf += 20
        if any(c.startswith("risk:") for c in cites):
            conf += 7
        if packet["fixes"]:
            conf += 5
        conf = max(conf - i * 45, 12)
        if change is not None and i == 0 and change.metadata.get("type") == "commit":
            title = change.text.split(" by ")[0].split(" ", 2)[2]
            text = f"PR #{change.metadata['pr']} ({title}) changed {', '.join(cluster.fields)}. Matches the playbook: {sk['name'].lower()}."
        else:
            text = f"{sk['name']}."
        if text in ruled:
            continue
        out.append(Theory(id=f"{cluster.id}-T{len(cluster.theories) + len(out) + 1}", text=text, confidence=conf,
                          cites=cites[:6], check_sql=sk.get("check")))
    return out


SYSTEM = ("You are the root-cause agent in a migration war room. Prove, don't guess. Cite card IDs from the packet for every claim. "
          "Never repeat a ruled-out theory. Reply as JSON: either {\"action\":\"expand_card\",\"id\":...} or "
          "{\"action\":\"final\",\"theories\":[{\"text\":...,\"confidence\":0-100,\"cites\":[ids],\"skill\":playbook file or null}]}. Max 3 theories.")


def model_theories(cluster, packet, agent: dict) -> list[Theory] | None:
    lines = [f"Cluster {cluster.id}: {cluster.title}"]
    for s in packet["steps"]:
        lines.append(f"[{s['name']}] {s['summary']} cites={s['cites']}")
    lines.append("Playbooks: " + ", ".join(f"{sk['file']} ({sk['name']})" for sk in packet["skills"]))
    ruled = [t.text for t in cluster.theories if t.status == "ruled_out"]
    if ruled:
        lines.append("Ruled out: " + "; ".join(ruled))
    ctx = "\n".join(lines)
    turns = int(agent.get("budget_turns", 4))
    for turn in range(turns):
        try:
            reply = gateway.llm_call("reason", SYSTEM + "\n" + agent["body"], ctx, max_tokens=500)
        except gateway.Offline:
            return None
        except Exception as e:  # network or quota: fall back rather than stall the room
            store.emit("root-cause", "model_error", {"error": str(e)[:200]})
            return None
        if reply.get("action") == "expand_card":
            card = memory.get(reply.get("id", ""))
            store.emit("root-cause", "tool", {"tool": "expand_card", "id": reply.get("id")})
            ctx += f"\n[expand {reply.get('id')}] {card['text'] if card else 'not found'}"
            continue
        theories = []
        for i, t in enumerate(reply.get("theories", [])[:3]):
            cites = [c for c in t.get("cites", []) if c in packet["cards"] or memory.get(c)]
            if not cites or t.get("text") in ruled:  # citation gate + ruled-out hook
                store.emit("hooks", "citation_gate_rejected", {"text": t.get("text")})
                continue
            sk = next((s for s in packet["skills"] if s["file"] == t.get("skill")), packet["skills"][0] if packet["skills"] else {})
            theories.append(Theory(id=f"{cluster.id}-T{len(cluster.theories) + i + 1}", text=t["text"],
                                   confidence=int(t.get("confidence", 50)), cites=cites, check_sql=sk.get("check")))
        if theories:
            return theories
        ctx += "\nEvery theory must cite at least one card ID from the packet."
    return None


def investigate(cluster_id: str, world, actor: str = "you"):
    cluster = store.clusters[cluster_id]
    agent = world.agents["root-cause"]
    with store.lock:
        if cluster.agent and cluster.lease_until > time.time() and cluster.agent != "root-cause":
            return cluster  # someone else holds the lease
        cluster.agent, cluster.lease_until = "root-cause", time.time() + 300
        cluster.status = "investigating"
        cluster.step = "Building the evidence packet"
        store.activity("root-cause", f"On {cluster.id}, building evidence")
        store.emit(actor, "investigate", {"cluster": cluster.id})
    packet = preflight(cluster, world)
    with store.lock:
        cluster.evidence = packet["steps"]
        cluster.step = "Ranking theories"
        store.activity("root-cause", f"On {cluster.id}, ranking theories")
    theories = model_theories(cluster, packet, agent)
    source = "model"
    if not theories:
        theories, source = offline_theories(cluster, packet), "playbooks (model offline)"
    with store.lock:
        cluster.theories = [t for t in cluster.theories if t.status != "open"] + theories
        cluster.evidence_ms = packet["ms"]
        top = next((t for t in theories if t.check_sql), None)
        if top:
            cluster.status = "needs_check"
            cluster.step = "Waiting on you to run its check"
            store.activity("root-cause", f"{cluster.id}, wants to run its check")
            store.add_approval(Approval(id=f"ap-check-{cluster.id}", kind="run_check", agent="root-cause", cluster=cluster.id,
                                        title=f"Run the {cluster.id} check on the replica", detail=top.check_sql,
                                        payload={"theory": top.id}))
        else:
            cluster.step = "Needs a human, evidence is weak"
            store.add_approval(Approval(id=f"ap-help-{cluster.id}", kind="help", agent="root-cause", cluster=cluster.id,
                                        title=f"{cluster.id}: evidence is weak, who knows this area?"))
        store.emit("root-cause", "theories", {"cluster": cluster.id, "source": source, "theories": [t.text for t in theories]})
    return cluster


# ---------------- check runner ----------------

def run_check(sql: str) -> dict:
    if not re.match(r"^\s*select\b", sql, re.I) or ";" in sql.strip().rstrip(";"):
        raise ValueError("Only single SELECT statements are allowed")
    con = sqlite3.connect(f"file:{REPLICA}?mode=ro", uri=True)
    deadline = time.time() + 5
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 1000)
    t0 = time.perf_counter()
    try:
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchmany(20)
    finally:
        con.close()
    return {"columns": cols, "rows": rows, "ms": round((time.perf_counter() - t0) * 1000, 1)}


def approve_check(cluster_id: str, approver: str, theory_id: str | None = None):
    cluster = store.clusters[cluster_id]
    theory = next(t for t in cluster.theories if (t.id == theory_id if theory_id else t.status == "open" and t.check_sql))
    result = run_check(theory.check_sql)
    affected = result["rows"][0][0] if result["rows"] else 0
    with store.lock:
        theory.check_result = f"{affected:,} rows match. Replica, SELECT only, {result['ms']}ms, approved by {approver}."
        if affected and affected > 0:
            theory.status, cluster.status = "confirmed", "confirmed"
            cluster.step = "Cause confirmed by the data"
            store.activity("root-cause", f"{cluster.id} confirmed")
            memory.add("human", [{"id": f"fix:{cluster.id}", "text": f"Confirmed cause for {cluster.title}: {theory.text}",
                                  "metadata": {"type": "confirmed", "cluster": cluster.id, "fields": cluster.fields}}])
        else:
            theory.status = "refuted"
            cluster.status = "investigating"
            cluster.step = "Check came back empty, theory refuted"
        cluster.agent = None
        store.emit(approver, "check_run", {"cluster": cluster.id, "affected": affected, "sql": theory.check_sql})
    return result


def rule_out(cluster_id: str, theory_id: str, who: str, reason: str = ""):
    cluster = store.clusters[cluster_id]
    with store.lock:
        t = next(t for t in cluster.theories if t.id == theory_id)
        t.status, t.ruled_out_by, t.reason = "ruled_out", who, reason
        memory.add("human", [{"id": f"ruled:{theory_id}", "text": f"Ruled out for {cluster_id}: {t.text}. {reason}",
                              "metadata": {"type": "ruled_out", "cluster": cluster_id}}])
        for a in store.approvals.values():
            if a.cluster == cluster_id and a.kind == "run_check" and a.status == "pending" and a.payload.get("theory") == theory_id:
                a.status = "dropped"
        store.emit(who, "ruled_out", {"cluster": cluster_id, "theory": t.text})
