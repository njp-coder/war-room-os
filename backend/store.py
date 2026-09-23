"""Event log (source of truth) plus in-memory projections for the UI.

Every action by a person or an agent is appended as an event. The board state
below is rebuilt from events, so the war room can be replayed later.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from .paths import DATA

DB = DATA / "events.db"
GO_LIVE_MIN = 9 * 60  # go-live was Day 1, 09:00


def clock_label(minute: int) -> str:
    absolute = GO_LIVE_MIN + minute
    return f"Day {absolute // 1440 + 1}, {(absolute % 1440) // 60:02d}:{absolute % 60:02d}"


@dataclass
class Theory:
    id: str
    text: str
    confidence: int
    cites: list[str]
    check_sql: str | None = None
    status: str = "open"  # open | confirmed | ruled_out | refuted
    ruled_out_by: str | None = None
    reason: str | None = None
    check_result: str | None = None


@dataclass
class Cluster:
    id: str
    title: str
    symptom: str
    service: str
    fields: list[str]
    ticket_ids: list[str] = field(default_factory=list)
    user_ids: list[str] = field(default_factory=list)
    first_seen: int = 0
    status: str = "new"  # new | investigating | needs_check | confirmed | fix_in_review | resolved
    owner: str | None = None  # person id
    agent: str | None = None  # agent currently holding the lease
    lease_until: float = 0.0
    client_affected: bool = False
    clients: list[str] = field(default_factory=list)
    theories: list[Theory] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)  # ordered chain steps
    step: str = ""  # live activity line
    evidence_ms: float = 0.0
    assignment: dict | None = None
    signature: str | None = None  # nearest playbook, used by triage


@dataclass
class Approval:
    id: str
    kind: str  # run_check | assign | client_update | rollback | decision
    agent: str
    cluster: str | None
    title: str
    detail: str = ""
    payload: dict = field(default_factory=dict)
    needs: str = "lead"  # who can approve: lead | cto | account_owner
    status: str = "pending"


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        DB.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(DB, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, ts REAL, minute INT, actor TEXT, kind TEXT, data TEXT)")
        self.reset()

    def reset(self):
        with self.lock:
            self.db.execute("DELETE FROM events")
            self.db.commit()
            self.minute = 0
            self.clusters: dict[str, Cluster] = {}
            self.tickets: dict[str, dict] = {}
            self.approvals: dict[str, Approval] = {}
            self.decisions: list[dict] = []
            self.rules: list[dict] = []
            self.transcript: list[dict] = []
            self.agent_activity: dict[str, str] = {}
            self.handovers: list[dict] = []
            self.client_updates: list[dict] = []
            self.seq = 0

    def emit(self, actor: str, kind: str, data: dict | None = None):
        with self.lock:
            self.seq += 1
            self.db.execute("INSERT INTO events VALUES(?,?,?,?,?,?)",
                            (self.seq, time.time(), self.minute, actor, kind, json.dumps(data or {})))
            self.db.commit()

    def events(self, limit: int = 200) -> list[dict]:
        rows = self.db.execute("SELECT seq, minute, actor, kind, data FROM events ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [{"seq": s, "time": clock_label(m), "actor": a, "kind": k, "data": json.loads(d)} for s, m, a, k, d in rows]

    def say(self, speaker: str, text: str, kind: str = "speech", **extra):
        line = {"id": f"l{len(self.transcript)}", "speaker": speaker, "text": text, "time": clock_label(self.minute),
                "kind": kind, **extra}
        self.transcript.append(line)
        self.emit(speaker, "said", {"text": text, "kind": kind})
        return line

    def activity(self, agent: str, text: str):
        self.agent_activity[agent] = text

    def add_approval(self, a: Approval):
        # one pending approval per (kind, cluster)
        for other in self.approvals.values():
            if other.status == "pending" and other.kind == a.kind and other.cluster == a.cluster:
                return other
        self.approvals[a.id] = a
        self.emit(a.agent, "approval_requested", asdict(a))
        return a


store = Store()
