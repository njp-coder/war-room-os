"""Adapters turn sources into context cards and add them to memory.

Adapter interface: fetch() -> list[(index, card)]. Files are live today;
GitHub/Jira/Slack/Sentry/Datadog slot in behind the same interface.
Entity tags come from a dictionary built from the schemas, CODEOWNERS and
batches. No model calls during ingestion.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .memory import memory

HERE = Path(__file__).parent
DATA = HERE / "demo" / "data"


def parse_md(path: Path) -> dict:
    text = path.read_text()
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    meta["body"] = m.group(2).strip()
    meta["file"] = path.stem
    return meta


def load_skills() -> list[dict]:
    return [parse_md(p) for p in sorted((HERE / "skills").glob("*.md"))]


def load_agents() -> dict[str, dict]:
    return {p.stem: parse_md(p) for p in sorted((HERE / "agents").glob("*.md"))}


class Lumen:
    """Loaded demo world. Stands in for real systems."""

    def __init__(self):
        self.d = json.loads((DATA / "lumen.json").read_text())
        self.users = {u["user_id"]: u for u in json.loads((DATA / "users.json").read_text())}
        self.tickets = json.loads((DATA / "tickets.json").read_text())
        self.people = {p["id"]: p for p in self.d["people"]}
        self.fields = [m["field"] for m in self.d["mapping"]]
        self.skills = load_skills()
        self.agents = load_agents()

    def tag(self, text: str) -> dict:
        """Dictionary tagger: fields, batches, services mentioned in text."""
        low = text.lower()
        fields = [f for f in self.fields if f in low or f.split(".")[1].replace("_", " ") in low]
        batches = re.findall(r"batch-\d", low)
        return {"fields": sorted(set(fields)), "batches": sorted(set(batches))}


def risk_map(world: Lumen) -> list[dict]:
    """Pre-mortem: check every mapping row against known migration failure types."""
    risks = []
    for m in world.d["mapping"]:
        kinds = []
        if m["nullable_legacy"] and m["required_v2"]:
            kinds.append("nullable field mapped to a required one")
        if "latin1" in m["legacy"] or "utf8" in m["v2"]:
            kinds.append("character encoding change")
        if "tz" in m["legacy"] or "UTC" in m["v2"] or "timestamptz" in m["v2"]:
            kinds.append("timezone change")
        if m["legacy"].startswith("enum") and m["v2"].startswith("enum"):
            kinds.append("enum narrowed, unmapped values fall back")
        if "not migrated" in m["v2"]:
            kinds.append("soft-deleted rows may arrive as active")
        for k in kinds:
            risks.append({"field": m["field"], "kind": k, "note": m["note"]})
    return risks


def ingest(world: Lumen) -> dict:
    d = world.d
    k, c, s, p, intents = [], [], [], [], []

    for path, lines in d["code"].items():
        k.append({"id": f"code:{path}", "text": f"{path}\n" + "\n".join(lines),
                  "metadata": {"type": "code", "file": path, **world.tag(" ".join(lines) + " " + path)}})
    for m in d["mapping"]:
        k.append({"id": f"map:{m['field']}", "text": f"Mapping {m['field']}: legacy {m['legacy']} to v2 {m['v2']}. {m['note']}",
                  "metadata": {"type": "mapping", "fields": [m["field"]]}})
    for inc in d["incidents"]:
        k.append({"id": f"inc:{inc['id']}", "text": f"Past incident {inc['title']}. {inc['text']}", "metadata": {"type": "incident"}})
    for i, r in enumerate(risk_map(world)):
        k.append({"id": f"risk:{r['field']}:{i}", "text": f"Risk before go-live: {r['field']} {r['kind']}. {r['note']}",
                  "metadata": {"type": "risk", "fields": [r["field"]]}})
    for sk in world.skills:
        k.append({"id": f"skill:{sk['file']}", "text": sk["signals"],
                  "metadata": {"type": "skill", "fields": [sk["fields"]]}})

    for cm in d["commits"]:
        c.append({"id": f"commit:{cm['id']}", "text": f"PR #{cm['pr']} {cm['title']} by {cm['author']}, reviewed by {cm['reviewer']}. "
                  f"Files {', '.join(cm['files'])}. Review: {cm['review']}",
                  "metadata": {"type": "commit", "minute": cm["minute"], "author": cm["author"], "reviewer": cm["reviewer"],
                               "fields": cm["fields"], "files": cm["files"], "pr": cm["pr"]}})
    for dp in d["deploys"]:
        c.append({"id": f"deploy:{dp['id']}", "text": f"Deploy {dp['id']}: {dp['title']}", "metadata": {"type": "deploy", "minute": dp["minute"]}})
    for b in d["batches"]:
        c.append({"id": f"batch:{b['id']}", "text": f"Migration {b['id']}: {b['rule']}",
                  "metadata": {"type": "batch", "minute": b["start"], "batches": [b["id"]]}})

    for lg in d["logs"]:
        s.append({"id": f"log:{lg['id']}", "text": f"Log pattern {lg['template']} ({lg['count']} hits)",
                  "metadata": {"type": "log", "minute": lg["first"], "service": lg["service"], "fields": lg["fields"], "count": lg["count"]}})

    for person in d["people"]:
        hist = [h["text"] for h in d["history"] if h["person"] == person["id"]]
        p.append({"id": f"person:{person['id']}", "text": f"{person['name']}, {person['role']}. Owns {', '.join(person['owns']) or 'nothing'}. "
                  f"Handled: {'; '.join(hist) or 'no history'}",
                  "metadata": {"type": "person", "person": person["id"], "shift": person["shift"],
                               "client_facing": str(person["client_facing"]).lower()}})

    examples = {
        "take": ["take cluster 3", "root cause take c2", "investigate cluster four", "look into the billing cluster", "pick up c1"],
        "rule_out": ["rule out the cache theory", "it's not the cache", "drop that theory", "that's not it, rule it out"],
        "decision_query": ["what did we decide about soft-deleted users", "remind me what we agreed on refunds", "did we decide anything about batch 6"],
        "status": ["status", "where are we", "give me the status", "what's open right now"],
        "run_check": ["run the check", "go ahead and run it", "run the query on the replica"],
    }
    for intent, phrases in examples.items():
        for i, ph in enumerate(phrases):
            intents.append({"id": f"intent:{intent}:{i}", "text": ph, "metadata": {"type": "intent", "intent": intent}})

    for name, cards in [("knowledge", k), ("changes", c), ("signals", s), ("people", p), ("intents", intents)]:
        memory.add(name, cards)
    return {"knowledge": len(k), "changes": len(c), "signals": len(s), "people": len(p), "intents": len(intents)}
