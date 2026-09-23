"""War Room OS API. Run: uvicorn backend.app:app --reload --port 8010"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import api_v2, auth, call, context as ctx, db, dispatcher, gateway, rootcause  # noqa: E402
from .seed import seed  # noqa: E402
from .ingest import Lumen, ingest  # noqa: E402
from .memory import memory  # noqa: E402
from .store import store, clock_label  # noqa: E402
from .triage import triage  # noqa: E402

app = FastAPI(title="War Room OS")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.middleware("http")(auth.middleware)  # every /api route needs a session (see auth.PUBLIC)

world = Lumen()
ingest_counts = ingest(world)
seed(world)
for _p in db.q("SELECT id FROM projects WHERE demo=0"):
    ctx.load_cards(_p["id"])  # warm each project's isolated memory
app.include_router(auth.router)
app.include_router(api_v2.router)
from . import monitor as _monitor, sources as _sources, experts as _experts  # noqa: F401  (creates tables)  # noqa: E402,F401
_monitor.start()  # watches every project with a monitor connection, within its guardrails
sim = {"running": False, "done": 0, "total": len(world.tickets)}
ROUTE_AT = 8  # tickets before the dispatcher routes a cluster


def _simulate(speed: float):
    sim.update(running=True, done=0)
    last_minute = 0
    for t in world.tickets:
        if not sim["running"]:
            break
        gap = max(t["minute"] - last_minute, 0)
        time.sleep(min(gap / speed, 0.25) + 0.02)
        last_minute = t["minute"]
        store.minute = t["minute"]
        ticket = {k: v for k, v in t.items() if not k.startswith("_")}  # ground truth never reaches agents
        cluster = triage(ticket, world)
        sim["done"] += 1
        if len(cluster.ticket_ids) == ROUTE_AT:
            packet = rootcause.preflight(cluster, world)  # code only, 0 tokens
            with store.lock:
                cluster.evidence = packet["steps"]
            dispatcher.route(cluster.id, world)
        elif len(cluster.ticket_ids) > ROUTE_AT and cluster.client_affected:
            _ensure_client_update(cluster)
    sim["running"] = False
    store.emit("simulator", "flood_done", {"tickets": sim["done"]})


def _ensure_client_update(cluster):
    if not any(a.kind == "client_update" and a.cluster == cluster.id and a.status in ("pending", "approved")
               for a in store.approvals.values()):
        dispatcher.draft_client_update(cluster, world)


def _person(pid: str | None) -> str | None:
    return world.people[pid]["name"] if pid and pid in world.people else None


def _cluster_row(c) -> dict:
    top = next((t for t in c.theories if t.status in ("open", "confirmed")), None)
    return {"id": c.id, "title": c.title, "count": len(c.ticket_ids), "status": c.status, "owner": _person(c.owner),
            "agent": c.agent, "client_affected": c.client_affected, "step": c.step, "first_seen": clock_label(c.first_seen),
            "top_theory": top.text if top else None, "confidence": top.confidence if top else None, "fields": c.fields}


def _people() -> list[dict]:
    out = []
    for p in world.people.values():
        doing = next((f"on {c.id}" for c in store.clusters.values() if c.owner == p["id"]), None)
        out.append({"id": p["id"], "name": p["name"], "role": p["role"], "level": p["level"], "shift": p["shift"],
                    "hours": p["hours_today"], "over_cap": p["hours_today"] >= dispatcher.HOURS_CAP,
                    "client_facing": p["client_facing"], "doing": doing})
    return out


def _agents() -> list[dict]:
    return [{"id": k, "name": a["name"], "short": a.get("short", k[:2].upper()), "trust": a.get("trust"),
             "activity": store.agent_activity.get(k, "Idle")} for k, a in world.agents.items()]


def _approvals() -> list[dict]:
    return [asdict(a) for a in store.approvals.values() if a.status == "pending"]


def _brief() -> dict:
    needs = [a for a in _approvals() if a["needs"] in ("cto",) or a["kind"] in ("client_update",)]
    health = []
    for p in world.people.values():
        if p["hours_today"] >= dispatcher.HOURS_CAP or p["shift"] != "on":
            health.append({"name": p["name"], "note": f"{p['hours_today']}h today, " + ("off shift" if p["shift"] != "on" else "over cap")})
        elif sum(1 for c in store.clusters.values() if c.owner == p["id"]) >= 2:
            health.append({"name": p["name"], "note": f"{sum(1 for c in store.clusters.values() if c.owner == p['id'])} clusters, {p['hours_today']}h today"})
    return {"needs_you": needs, "clusters": [_cluster_row(c) for c in store.clusters.values()], "health": health,
            "time": clock_label(store.minute)}


@app.get("/api/health")
def health_check():
    return {"ok": True}


@app.get("/api/state")
def state():
    with store.lock:
        clusters = sorted(store.clusters.values(), key=lambda c: -len(c.ticket_ids))
        return {
            "project": world.d["migration"], "company": world.d["company"], "clock": clock_label(store.minute),
            "memory": memory.status(), "model": gateway.status(), "sim": sim, "ingested": ingest_counts,
            "tickets": len(store.tickets), "clusters": [_cluster_row(c) for c in clusters],
            "confirmed": sum(1 for c in clusters if c.status == "confirmed"),
            "people": _people(), "agents": _agents(), "approvals": _approvals(),
            "transcript": store.transcript[-40:], "decisions": store.decisions,
        }


@app.get("/api/clusters/{cid}")
def cluster_detail(cid: str):
    c = store.clusters.get(cid)
    if not c:
        raise HTTPException(404)
    with store.lock:
        cards = {}
        for s in c.evidence:
            for cite in s["cites"]:
                card = memory.get(cite)
                if card:
                    cards[cite] = card["text"]
        return {**_cluster_row(c), "service": c.service, "fields": c.fields, "clients": c.clients,
                "theories": [asdict(t) for t in c.theories], "evidence": c.evidence, "cards": cards,
                "evidence_ms": c.evidence_ms, "assignment": c.assignment,
                "handover": _person((c.assignment or {}).get("handover")),
                "sample_tickets": [store.tickets[t]["text"] for t in c.ticket_ids[:5]]}


@app.get("/api/brief")
def brief():
    with store.lock:
        return _brief()


class SimIn(BaseModel):
    speed: float = 180.0  # war minutes per real second


@app.post("/api/sim/start")
def sim_start(body: SimIn):
    if sim["running"]:
        return sim
    store.reset()
    for i in [k for k, c in memory.cards.items() if c["metadata"].get("index") == "signals" and c["metadata"].get("type") in ("cluster", "ticket")]:
        memory.cards.pop(i, None)
        memory.local["signals"].delete([i])
    threading.Thread(target=_simulate, args=(body.speed,), daemon=True).start()
    return sim


@app.post("/api/sim/stop")
def sim_stop():
    sim["running"] = False
    return sim


@app.post("/api/clusters/{cid}/investigate")
def investigate(cid: str):
    if cid not in store.clusters:
        raise HTTPException(404)
    store.say("You", f"Root cause, take cluster {cid[1:]}.")
    threading.Thread(target=_investigate_and_route, args=(cid,), daemon=True).start()
    return {"ok": True}


def _investigate_and_route(cid: str):
    c = rootcause.investigate(cid, world)
    top = next((t for t in c.theories if t.status == "open"), None)
    if top:
        risk = any(x.startswith("risk:") for s in c.evidence for x in s["cites"])
        store.say("War Room", f"On it. Top theory at {top.confidence}%." + (" This matches a risk map item from before go-live." if risk else ""),
                  kind="agent")
    if not c.owner:
        dispatcher.route(cid, world)


@app.post("/api/clusters/{keep}/merge/{drop}")
def merge(keep: str, drop: str):
    with store.lock:
        a, b = store.clusters.get(keep), store.clusters.pop(drop, None)
        if not a or not b:
            raise HTTPException(404)
        a.ticket_ids += b.ticket_ids
        a.user_ids += b.user_ids
        a.clients = sorted(set(a.clients + b.clients))
        a.client_affected = a.client_affected or b.client_affected
        a.first_seen = min(a.first_seen, b.first_seen)
        for ap in store.approvals.values():
            if ap.cluster == drop and ap.status == "pending":
                ap.status = "dropped"
        memory.cards.pop(f"cluster:{drop}", None)
        memory.local["signals"].delete([f"cluster:{drop}"])
        store.emit("You", "merged", {"keep": keep, "drop": drop})
        if a.client_affected:
            _ensure_client_update(a)
    return {"ok": True}


class RuleOut(BaseModel):
    reason: str = ""


@app.post("/api/clusters/{cid}/theories/{tid}/rule_out")
def rule_out(cid: str, tid: str, body: RuleOut):
    rootcause.rule_out(cid, tid, "You", body.reason)
    threading.Thread(target=rootcause.investigate, args=(cid, world), daemon=True).start()
    return {"ok": True}


class Decide(BaseModel):
    action: str  # approve | dismiss


@app.post("/api/approvals/{aid}")
def decide(aid: str, body: Decide):
    a = store.approvals.get(aid)
    if not a or a.status != "pending":
        raise HTTPException(404)
    if body.action == "dismiss":
        a.status = "dismissed"
        store.emit("You", "approval_dismissed", {"id": aid})
        return {"ok": True}
    a.status = "approved"
    store.emit("You", "approval_approved", {"id": aid})
    result = None
    if a.kind == "run_check":
        result = rootcause.approve_check(a.cluster, "you", a.payload.get("theory"))
        c = store.clusters[a.cluster]
        t = next(t for t in c.theories if t.id == a.payload.get("theory"))
        store.say("War Room", f"{a.cluster} check: {t.check_result}", kind="agent")
    elif a.kind == "assign":
        dispatcher.accept(a)
    elif a.kind == "decision":
        call.pin_decision(a.detail, a.payload.get("speaker", "You"))
    elif a.kind == "client_update":
        store.client_updates.append({"cluster": a.cluster, "text": a.detail, "approved_by": "You"})
        store.say("War Room", f"Client update for {a.cluster} approved. The account owner sends it; the system never messages clients.", kind="agent")
    return {"ok": True, "result": result}


class Say(BaseModel):
    text: str
    speaker: str = "You"


@app.post("/api/say")
def say(body: Say):
    result = call.handle(body.speaker, body.text)
    if result.get("intent") == "take" and result.get("cluster") in store.clusters:
        threading.Thread(target=_investigate_and_route, args=(result["cluster"],), daemon=True).start()
    if result.get("intent") == "run_check":
        pending = next((a for a in store.approvals.values() if a.kind == "run_check" and a.status == "pending"), None)
        if pending:
            store.say("War Room", f"The {pending.cluster} check needs your approval. Use Run check.", kind="agent")
    return result


@app.get("/api/events")
def events(limit: int = 100):
    return store.events(limit)
