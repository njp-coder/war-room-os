"""Tool registry. Every agent action is one of these, called through the harness loop.

risk:  read  = reads memory, graph or a read-only replica
       ask   = talks to a human (thread, help request)
       write = changes shared state (proposals, bugs)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from .. import db, graph
from ..memory import memory_for


@dataclass
class Tool:
    name: str
    risk: str
    description: str
    fn: Callable
    scope: str = "any repo"   # honest reach: any repo | needs staging URL | demo only | model only


REGISTRY: dict[str, Tool] = {}
SCOPES = {
    "seen_before": "any repo (simple title match)",
    "expand_card": "model only",
    "run_repro": "needs staging DB or URL",
    "run_checks": "needs staging DB or URL",
}


def tool(name: str, risk: str, description: str):
    def wrap(fn):
        REGISTRY[name] = Tool(name, risk, description, fn, SCOPES.get(name, "any repo"))
        return fn
    return wrap


# ---------------- shared context the tools read and write ----------------

@dataclass
class Ctx:
    project: str
    bug: dict | None = None
    release: dict | None = None
    scratch: dict | None = None  # what earlier tool calls in this run produced

    def __post_init__(self):
        self.scratch = self.scratch or {}


def _file_for(project: str, cid: str) -> str | None:
    if cid.startswith("code:"):
        return "file:lumen:" + cid[5:]
    if cid.startswith("sym:"):
        return "file:" + cid[4:].split("#")[0]
    if cid.startswith("route:"):
        h = graph.neighbors(project, cid, {"handled_by"}, "out")
        return "file:" + h[0]["id"][4:].split("#")[0] if h else None
    return cid if cid.startswith("file:") else None


# ---------------- root cause ----------------

@tool("search_code", "read", "Moss search over code, endpoints, fields and schema mappings for the bug text.")
def search_code(ctx: Ctx, query: str | None = None, k: int = 4):
    text = query or f"{ctx.bug['title']} {ctx.bug['body']}"
    from ..tracks import allowed
    see = {ctx.bug["track"]} if ctx.bug.get("track") else set()  # shared context, plus the owning team's
    hits = [h for h in memory_for(ctx.project).search("knowledge", text, top_k=12)
            if h.metadata.get("type") in ("symbol", "endpoint", "field", "code", "mapping") and allowed(h.metadata, see)][:k]
    ctx.scratch["code"] = [{"id": h.id, "text": h.text} for h in hits]
    return {"cites": [h.id for h in hits], "summary": ", ".join(h.id.split(":", 1)[-1].split("#")[-1].split("/")[-1] for h in hits) or "nothing found"}


@tool("changes_for", "read", "PRs that touched the matched code or altered the matched fields, ranked by evidence paths.")
def changes_for(ctx: Ctx):
    from ..releases import linked_prs
    code = ctx.scratch.get("code", [])
    in_release = {p["id"] for p in linked_prs(ctx.release)} if ctx.release else set()
    votes: dict[str, float] = {}
    for i, h in enumerate(code):
        cid = h["id"]
        if cid.startswith(("map:", "field:")):
            fid = "field:" + cid.split(":", 1)[1]
            for t in graph.neighbors(ctx.project, fid, {"has_field"}, "in"):  # the model file that defines the table
                src = json.loads((db.one("SELECT props FROM nodes WHERE project=? AND id=?", (ctx.project, t["id"])) or {"props": "{}"})["props"]).get("source")
                if src:
                    for fnode in db.q("SELECT id FROM nodes WHERE project=? AND type='file' AND label=?", (ctx.project, src)):
                        for pr in graph.neighbors(ctx.project, fnode["id"], {"touches"}, "in"):
                            votes[pr["id"]] = votes.get(pr["id"], 0) + 1 + (0.5 if pr["id"] in in_release else 0)
            for e in db.q("SELECT src, source FROM edges WHERE project=? AND dst=? AND type='alters' AND valid_to IS NULL", (ctx.project, fid)):
                if str(e["source"]).startswith("pr:"):
                    votes[e["source"]] = votes.get(e["source"], 0) + 2 + (0.5 if e["source"] in in_release else 0)
                for pr in graph.neighbors(ctx.project, e["src"], {"touches"}, "in"):
                    votes[pr["id"]] = votes.get(pr["id"], 0) + 1
            continue
        f = _file_for(ctx.project, cid)
        if f:
            for pr in graph.neighbors(ctx.project, f, {"touches"}, "in"):
                votes[pr["id"]] = votes.get(pr["id"], 0) + 1 + (0.5 if pr["id"] in in_release else 0) + 1 / (10 + i)
    ranked = sorted(votes, key=lambda p: -votes[p])[:3]
    ctx.scratch["changes"] = ranked
    ctx.scratch["in_release"] = in_release
    labels = [(graph.node(ctx.project, p) or {"label": p})["label"] for p in ranked]
    return {"cites": ranked, "summary": "; ".join(l + (" (in this release)" if p in in_release else "") for p, l in zip(ranked, labels)) or "no linked change"}


@tool("seen_before", "read", "Earlier fixed bugs in this project that match, to catch regressions.")
def seen_before(ctx: Ctx):
    b = ctx.bug
    prior = [x for x in db.q("SELECT id, title, env FROM bugs WHERE project=? AND id<>? AND status IN ('fixed','verified','closed')", (ctx.project, b["id"]))
             if x["env"] == "staging" and b["env"] == "production"]
    words = [w for w in b["title"].lower().split() if len(w) > 4]
    regress = [x for x in prior if any(w in x["title"].lower() for w in words)][:2]
    ctx.scratch["regress"] = regress
    return {"cites": [x["id"] for x in regress], "summary": ("possible regression of " + ", ".join(x["title"] for x in regress)) if regress else "no earlier fix matches"}


@tool("expand_card", "read", "Full text of one card by ID.")
def expand_card(ctx: Ctx, id: str):
    card = memory_for(ctx.project).get(id)
    return {"cites": [id] if card else [], "summary": (card["text"][:300] if card else "not found")}


@tool("theorize", "read", "Write the root-cause theory from the evidence gathered so far. Model if online, evidence-only otherwise.")
def theorize(ctx: Ctx):
    from .. import gateway, jev
    code, changes = ctx.scratch.get("code", []), ctx.scratch.get("changes", [])
    # Jev: a calibrated probability per candidate change, and for "none of these". Re-ranks the candidates.
    odds = None
    if changes:
        problem = f"{ctx.bug['title']}. {ctx.bug.get('body') or ''}\nCode that matches: " + "; ".join(h["text"][:160] for h in code)
        odds = jev.rank_causes(ctx.project, "bug", ctx.bug["id"], problem, changes, ctx.scratch.get("in_release", set()))
        if odds:
            changes = [pid for pid, _ in odds[0]]
            ctx.scratch["changes"] = changes
    steps = [
        {"name": "Where in the code", "summary": "; ".join(h["text"][:110] for h in code) or "No matching code.", "cites": [h["id"] for h in code]},
        {"name": "What changed", "summary": "; ".join((graph.node(ctx.project, p) or {"label": p})["label"] + (" (in this release)" if p in ctx.scratch.get("in_release", set()) else "")
                                                      for p in changes) or "No linked change found.", "cites": changes},
        {"name": "Seen before", "summary": ("Possible regression of " + ", ".join(x["title"] for x in ctx.scratch.get("regress", []))) if ctx.scratch.get("regress") else "No earlier fix matches.",
         "cites": [x["id"] for x in ctx.scratch.get("regress", [])]},
    ]
    if odds:
        ranked, p_none = odds
        steps.append({"name": "How sure (Jev)", "cites": [pid for pid, _ in ranked],
                      "summary": "; ".join(f"{(graph.node(ctx.project, pid) or {'label': pid})['label'][:60]}: {round(p * 100)}%" for pid, p in ranked)
                                 + (f"; something else: {round(p_none * 100)}%" if p_none is not None else "")})
    theory, source = None, "evidence"
    try:
        reply = gateway.llm_call("reason", "Root-cause agent. Use only the evidence. Cite card IDs. JSON: {\"text\":..., \"confidence\":0-100, \"cites\":[ids]}",
                                 "\n".join(f"[{s['name']}] {s['summary']} cites={s['cites']}" for s in steps) + f"\nBug: {ctx.bug['title']} {ctx.bug['body'][:400]}", 300)
        valid = [c for c in reply.get("cites", []) if any(c in s["cites"] for s in steps)]
        if reply.get("text") and valid:
            theory, source = {"text": reply["text"], "confidence": int(reply.get("confidence", 50)), "cites": valid}, "model"
    except gateway.Offline:
        pass
    except Exception:
        pass
    if not theory:
        if code and changes:
            where = code[0]["text"].split(":")[0].split("\n")[0]
            ch = (graph.node(ctx.project, changes[0]) or {"label": changes[0]})["label"]
            in_rel = changes[0] in ctx.scratch.get("in_release", set())
            theory = {"text": f"Most likely in {where}, last changed by PR {ch}" + (", which ships in this release." if in_rel else "."),
                      "confidence": 55 + (15 if in_rel else 0) + (10 if ctx.scratch.get("regress") else 0), "cites": [code[0]["id"], changes[0]]}
        else:
            theory = {"text": "Evidence is weak. Needs someone who knows this area.", "confidence": 20, "cites": []}
    if odds:
        # "How sure" is Jev's probability for the change the theory blames, not a self-reported number.
        ranked, p_none = odds
        pmap = dict(ranked)
        blamed = next((c for c in theory["cites"] if c in pmap), None)
        if blamed:
            theory["confidence"] = round(pmap[blamed] * 100)
        if p_none is not None and ranked and p_none > ranked[0][1]:
            theory["text"] += f" Jev thinks it's more likely something other than these changes ({round(p_none * 100)}%): check data and config too."
        theory["calibrated"] = "jev"
        source += "+jev"
    people = []
    for p in changes:
        people += [n["id"].split(":", 1)[1] for n in graph.neighbors(ctx.project, p, {"authored", "reviewed"}, "in")]
    from ..releases import _suggest_assignee
    result = {"steps": steps, "theory": theory, "source": source, "assignee": _suggest_assignee(ctx.project, people), "regression": bool(ctx.scratch.get("regress"))}
    if odds:
        result["jev"] = {"candidates": [{"id": pid, "label": (graph.node(ctx.project, pid) or {"label": pid})["label"], "p": p} for pid, p in odds[0]],
                         "none": odds[1]}
    db.x("UPDATE bugs SET evidence=? WHERE id=?", (json.dumps(result), ctx.bug["id"]))
    ctx.scratch["evidence"] = result
    return {"cites": theory["cites"], "summary": f"{theory['confidence']}%: {theory['text'][:160]}", "final": True, "theory": theory}


# ---------------- repro ----------------

@tool("build_repro", "read", "Draft reproduction steps from the evidence: affected accounts, entry point, data, expected vs actual.")
def build_repro(ctx: Ctx):
    from ..pipeline import build_repro as draft
    ev = ctx.scratch.get("evidence") or (json.loads(ctx.bug["evidence"]) if (ctx.bug.get("evidence") or "").startswith("{") else {"steps": []})
    rp = draft(ctx.project, ctx.bug, ev)
    db.x("UPDATE bugs SET repro=? WHERE id=?", (json.dumps(rp), ctx.bug["id"]))
    ctx.scratch["repro"] = rp
    return {"cites": rp.get("endpoints", []), "summary": f"{len(rp['steps'])} steps" + (", runnable" if rp.get("run") else ", not runnable by me")}


@tool("run_repro", "read", "Run the repro if it is read-only (staging replica query or GET on staging).")
def run_repro(ctx: Ctx):
    from ..pipeline import run_repro as run
    res = run(ctx.project, ctx.bug["id"], "")
    ctx.scratch["repro_result"] = res
    return {"cites": [], "summary": res["summary"]}


@tool("ask_human", "ask", "Hand work to a human tester with the draft and the reason, and post in the thread.")
def ask_human(ctx: Ctx, why: str = ""):
    from ..testing import ask_human_repro
    rp = ctx.scratch.get("repro") or {}
    tid = ask_human_repro(ctx.project, {**ctx.bug, "repro": json.dumps(rp)}, why or "there's nothing safe for me to run.")
    return {"cites": [tid], "summary": f"asked a tester (check {tid})"}


# ---------------- dispatcher ----------------

@tool("estimate_eta", "read", "Estimate fix hours from files changed, callers, data backfill, tests, and past fixes.")
def estimate_eta(ctx: Ctx):
    from ..pipeline import estimate_eta as est
    ev = ctx.scratch.get("evidence") or json.loads(ctx.bug["evidence"] or "{}")
    hours, why = est(ctx.project, ctx.bug, ev)
    ctx.scratch["eta"], ctx.scratch["eta_why"] = hours, why
    return {"cites": [], "summary": f"~{hours:g}h: {why}"}


@tool("propose_owner", "write", "Propose an owner by who wrote/reviewed/owns the code, availability and remaining hours.")
def propose_owner(ctx: Ctx):
    from ..pipeline import name, propose_owner as prop
    ev = ctx.scratch.get("evidence") or json.loads(ctx.bug["evidence"] or "{}")
    eta = ctx.scratch.get("eta", 2)
    p = prop(ctx.project, ctx.bug["id"], ev, eta)
    db.x("UPDATE bugs SET proposed=?, proposed_why=?, eta_agent=?, eta_agent_why=?, stage='proposed' WHERE id=?",
         (p["user"], json.dumps(p), eta, ctx.scratch.get("eta_why", ""), ctx.bug["id"]))
    ctx.scratch["proposal"] = p
    return {"cites": [], "summary": (f"{name(p['user'])}: {p['why']}" if p["user"] else p["why"])}


# ---------------- tester ----------------

@tool("plan_checks", "read", "Plan checks from the pre-mortem; route automatable ones to the agent and the rest to human testers.")
def plan_checks(ctx: Ctx):
    from ..testing import plan
    tests = plan(ctx.release)
    agent = sum(1 for t in tests if t["owner_kind"] == "agent")
    return {"cites": [], "summary": f"{len(tests)} checks: {agent} for me, {len(tests) - agent} for human testers"}


@tool("run_checks", "read", "Run the agent's checks on staging (read-only) and file bugs with evidence for failures.")
def run_checks(ctx: Ctx):
    from ..testing import run_agent
    out = run_agent(ctx.release)
    ctx.scratch["run"] = out
    return {"cites": [], "summary": f"ran {out['ran']}: {out['failed']} failed and filed, {out['passed']} passed", "final": True}


# ---------------- expert ----------------

def _bug_files(ctx: Ctx) -> list[str]:
    from ..experts import graph_file
    ev = ctx.scratch.get("evidence") or (json.loads(ctx.bug["evidence"]) if (ctx.bug.get("evidence") or "").startswith("{") else {})
    files = [graph_file(c) for s in ev.get("steps", []) for c in s.get("cites", [])]
    # the data the bug is about: its tables' model files, and migrations that touch those columns
    rp = json.loads(ctx.bug.get("repro") or "{}") if (ctx.bug.get("repro") or "").startswith("{") else {}
    for f in rp.get("fields", [])[:3]:
        t = graph.node(ctx.project, "table:" + f.split(".")[0])
        if t and t["props"].get("source"):
            files.insert(0, t["props"]["source"])
        files += [graph_file(n["id"]) for n in graph.neighbors(ctx.project, "field:" + f, {"uses_field", "writes_field"}, "in")[:3]]
    if not any(files):
        for c in [c for s in ev.get("steps", []) for c in s.get("cites", []) if c.startswith("pr:")][:1]:
            files += [r["path"] for r in db.q("SELECT path FROM pr_files WHERE project=? AND pr=?", (ctx.project, c))]
    return [f for f in dict.fromkeys(files) if f]


@tool("recall_lessons", "read", "Find the expert for the code this bug touches and recall what its seniors said about similar changes.")
def recall_lessons(ctx: Ctx):
    from ..experts import for_files
    files = _bug_files(ctx)
    e = for_files(ctx.project, files)
    ctx.scratch["expert_files"] = files
    if not e:
        return {"cites": [], "summary": "no expert covers this code yet"}
    from ..experts import recall
    ev = json.loads(ctx.bug["evidence"]) if (ctx.bug.get("evidence") or "").startswith("{") else {}
    hits = recall(ctx.project, e["id"], f"{ctx.bug['title']} {ctx.bug.get('body', '')} {(ev.get('theory') or {}).get('text', '')} {' '.join(files)}")
    return {"cites": [h["id"] for h in hits], "summary": f"{e['name']}: {len(hits)} lessons match"}


@tool("consult_expert", "ask", "Review the fix plan as the area's seniors would; post advice in the thread and ask a named senior to sign off on risky changes.")
def consult_expert(ctx: Ctx):
    from ..experts import consult
    from ..testing import note
    b = db.one("SELECT * FROM bugs WHERE id=?", (ctx.bug["id"],))
    files = ctx.scratch.get("expert_files") or _bug_files(ctx)
    ev = json.loads(b["evidence"]) if (b["evidence"] or "").startswith("{") else {}
    subject = f"{b['title']}. {b['body'] or ''} {(ev.get('theory') or {}).get('text', '')}"
    a = consult(ctx.project, "bug", b["id"], subject, files, b["assignee"] or b["proposed"])
    if not a:
        return {"cites": [], "summary": "no expert for this area", "final": True}
    note(ctx.project, "bug", b["id"], a["expert"], "agent", a["text"], a["signoff"])
    return {"cites": [l["id"] for l in a["lessons"]], "final": True,
            "summary": f"advised from {len(a['lessons'])} lessons" + (", asked a senior to sign off" if a["signoff"] else "")}


def describe(names: list[str]) -> str:
    return "\n".join(f"- {n} ({REGISTRY[n].risk}): {REGISTRY[n].description}" for n in names if n in REGISTRY)
