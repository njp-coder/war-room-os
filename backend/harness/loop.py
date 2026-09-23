"""The agent harness: one loop every agent runs through.

  role file (agents/*.md)  -> allowed tools, trust level, turn budget, model role
  policy                   -> the model picks the next tool (when a key is set), or a scripted plan does
  hooks                    -> before each call: allowlist, trust/approval, standing rules
                              after each call: trace + event log
                              before the final answer: citation gate
  trace                    -> every call recorded in `runs`, shown in the UI
"""
from __future__ import annotations

import json
import time

from .. import db, gateway
from ..ingest import load_agents
from .tools import REGISTRY, Ctx, describe

AGENTS = load_agents()
TRUST_ALLOWS = {"read": {"read", "ask"}, "propose": {"read", "ask", "write"}, "act": {"read", "ask", "write"}}

# Scripted policies: the same tools, in the order a careful engineer would use them. Used when no model key is set.
SCRIPTS = {
    "root-cause": lambda ctx: ["search_code", "changes_for", "seen_before", "theorize"],
    "repro": lambda ctx: ["build_repro", "run_repro" if (ctx.scratch.get("repro") or {}).get("run") else "ask_human"],
    "dispatcher": lambda ctx: ["estimate_eta", "propose_owner"],
    "tester": lambda ctx: ["plan_checks", "run_checks"],
    "expert": lambda ctx: ["recall_lessons", "consult_expert"],
}


class Blocked(Exception):
    pass


def _tools(agent: dict) -> list[str]:
    return [t.strip() for t in agent.get("tools", "").split(",") if t.strip()]


def pre_tool(agent_key: str, agent: dict, name: str, ctx: Ctx):
    if name not in _tools(agent):
        raise Blocked(f"{name} is not in {agent['name']}'s role file")
    tool = REGISTRY.get(name)
    if not tool:
        raise Blocked(f"unknown tool {name}")
    if tool.risk not in TRUST_ALLOWS.get(agent.get("trust", "read"), {"read"}):
        raise Blocked(f"{agent['name']} has trust '{agent.get('trust')}' and can't use a {tool.risk} tool")
    # standing rules: pinned decisions that forbid an action for this release
    if ctx.release:
        for d in db.q("SELECT text FROM decisions WHERE release=?", (ctx.release["id"],)):
            if f"no {name}" in d["text"].lower() or f"don't {name.replace('_', ' ')}" in d["text"].lower():
                raise Blocked(f"blocked by decision: {d['text']}")


def pre_output(result: dict):
    """Citation gate: a final answer that makes a claim must cite something that exists."""
    theory = result.get("theory")
    if theory and theory.get("confidence", 0) > 30 and not theory.get("cites"):
        raise Blocked("citation gate: a confident theory must cite evidence")


def run(agent_key: str, ctx: Ctx, owner_type: str, owner_id: str, task: str = "") -> dict:
    agent = AGENTS[agent_key]
    budget = int(agent.get("budget_turns", 4))
    rid = db.uid("run")
    online = gateway.status()["online"] and agent.get("role") not in (None, "none")
    policy = "model" if online else "scripted"
    db.x("INSERT INTO runs(id, project, agent, owner_type, owner_id, task, policy, status, steps, started) VALUES(?,?,?,?,?,?,?,?,?,?)",
         (rid, ctx.project, agent_key, owner_type, owner_id, task, policy, "running", "[]", time.time()))
    steps: list[dict] = []

    def save(status="running"):
        db.x("UPDATE runs SET steps=?, status=?, ended=? WHERE id=?", (json.dumps(steps, default=str), status,
                                                                       time.time() if status != "running" else None, rid))

    planned = SCRIPTS[agent_key](ctx) if agent_key in SCRIPTS else []
    i, final = 0, None
    while len(steps) < budget:
        if policy == "model":
            name, args = _model_next(agent, ctx, steps)
            if name is None:
                break
        else:
            planned = SCRIPTS[agent_key](ctx)  # re-plan: later steps can depend on earlier results
            if i >= len(planned):
                break
            name, args = planned[i], {}
            i += 1
        t0 = time.perf_counter()
        try:
            pre_tool(agent_key, agent, name, ctx)
            out = REGISTRY[name].fn(ctx, **args)
            if out.get("final"):
                pre_output(out)
            steps.append({"tool": name, "risk": REGISTRY[name].risk, "args": args, "summary": out.get("summary", ""),
                          "cites": out.get("cites", [])[:6], "ms": round((time.perf_counter() - t0) * 1000, 1), "ok": True})
            final = out if out.get("final") else final
        except Blocked as b:
            steps.append({"tool": name, "args": args, "summary": str(b), "ms": 0, "ok": False, "blocked": True})
        except Exception as e:
            steps.append({"tool": name, "args": args, "summary": f"error: {str(e)[:160]}", "ms": 0, "ok": False})
        save()
        if final and policy == "model":
            break
    save("done")
    return {"run": rid, "steps": steps, "final": final, "policy": policy, "scratch": ctx.scratch}


def _model_next(agent: dict, ctx: Ctx, steps: list[dict]) -> tuple[str | None, dict]:
    tools = _tools(agent)
    history = "\n".join(f"{s['tool']} -> {s['summary']}" for s in steps) or "(nothing yet)"
    subject = f"Bug: {ctx.bug['title']}. {ctx.bug.get('body', '')[:300]}" if ctx.bug else f"Release: {ctx.release['name'] if ctx.release else ''}"
    try:
        reply = gateway.llm_call(agent.get("role", "fast"), agent["body"] + "\nTools:\n" + describe(tools) +
                                 '\nReply JSON: {"tool": name, "args": {}} or {"done": true}.',
                                 f"{subject}\nSo far:\n{history}", 150)
    except Exception:
        return None, {}
    if reply.get("done") or reply.get("tool") not in tools:
        return None, {}
    return reply["tool"], reply.get("args") or {}


def runs_for(owner_type: str, owner_id: str) -> list[dict]:
    out = []
    for r in db.q("SELECT * FROM runs WHERE owner_type=? AND owner_id=? ORDER BY started", (owner_type, owner_id)):
        a = AGENTS.get(r["agent"], {})
        out.append({"id": r["id"], "agent": a.get("name", r["agent"]), "trust": a.get("trust"), "tools": _tools(a), "policy": r["policy"],
                    "status": r["status"], "steps": json.loads(r["steps"]), "ms": round(((r["ended"] or time.time()) - r["started"]) * 1000)})
    return out
