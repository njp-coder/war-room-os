"""Call agent: routes spoken or typed commands, flags decisions.

Intent matching is a Moss search over example phrases (0 model tokens).
Decision detection is trigger words; a human pins it.
"""
from __future__ import annotations

import re

from .memory import memory, f_eq
from .store import store, Approval

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
DECISION = re.compile(r"\b(let's|lets|we'll|we will|we skip|skip|decided|we're going with|going with|don't|do not)\b", re.I)


def _cluster_ref(text: str) -> str | None:
    m = re.search(r"\bc(?:luster)?\s*(\d+)\b", text, re.I)
    if m:
        return f"C{m.group(1)}"
    for w, n in WORDS.items():
        if re.search(rf"cluster {w}\b", text, re.I):
            return f"C{n}"
    hits = memory.search("signals", text, top_k=1, flt=f_eq("type", "cluster"))
    return hits[0].metadata["cluster"] if hits and hits[0].score > 0.2 else None


def handle(speaker: str, text: str) -> dict:
    store.say(speaker, text)
    hits = memory.search("intents", text, top_k=1)
    intent = hits[0].metadata["intent"] if hits and hits[0].score > 0.3 else None
    result = {"intent": intent}
    if intent == "take":
        result["cluster"] = _cluster_ref(text)
    elif intent == "rule_out":
        result["cluster"] = _cluster_ref(text)
    elif intent == "decision_query":
        found = memory.search("human", text, top_k=1, flt=f_eq("type", "decision"))
        reply = f"Pinned decision: {found[0].text}" if found and found[0].score > 0.1 else "No pinned decision on that yet."
        store.say("War Room", reply, kind="agent")
    elif intent == "status":
        open_ = [c for c in store.clusters.values() if c.status != "resolved"]
        confirmed = sum(1 for c in store.clusters.values() if c.status == "confirmed")
        store.say("War Room", f"{len(open_)} clusters open, {confirmed} causes confirmed.", kind="agent")
    if not intent and DECISION.search(text):
        store.add_approval(Approval(id=f"ap-decision-{len(store.decisions)}-{len(store.transcript)}", kind="decision",
                                    agent="call", cluster=None, title="Sounds like a decision. Pin it and every agent follows it.",
                                    detail=text, payload={"speaker": speaker}))
    return result


def pin_decision(text: str, speaker: str):
    d = {"id": f"dec-{len(store.decisions) + 1}", "text": text, "by": speaker}
    store.decisions.append(d)
    memory.add("human", [{"id": f"decision:{d['id']}", "text": text, "metadata": {"type": "decision"}}])
    store.emit(speaker, "decision_pinned", d)
    store.say("War Room", "Pinned. Every agent will follow it.", kind="agent")
