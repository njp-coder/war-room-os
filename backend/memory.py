"""Shared memory: Moss indexes, with a local fallback for development.

Every card is {id, text, metadata}. Metadata values are strings (Moss filters
compare strings). Lists are stored comma-joined.

Backend is chosen at startup:
  - "moss"  when MOSS_PROJECT_ID and MOSS_PROJECT_KEY are set (real sub-10ms search)
  - "local" otherwise: a small TF-IDF index so the app runs without credentials.
The UI shows which backend is live, so the demo never claims Moss when it isn't.
"""
from __future__ import annotations

import asyncio
import sys
import inspect
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

INDEXES = ["knowledge", "changes", "signals", "human", "people", "intents", "experts"]

_TOKEN = re.compile(r"[a-z0-9_.]+")


@dataclass
class Hit:
    id: str
    text: str
    metadata: dict
    score: float
    index: str


@dataclass
class Stats:
    latencies_ms: list = field(default_factory=list)
    count: int = 0
    started: float = field(default_factory=time.time)

    def record(self, ms: float):
        self.count += 1
        self.latencies_ms.append(ms)
        self.latencies_ms = self.latencies_ms[-500:]

    def p50(self) -> float:
        if not self.latencies_ms:
            return 0.0
        s = sorted(self.latencies_ms)
        return s[len(s) // 2]


def _tok(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _match(meta: dict, flt: dict | None) -> bool:
    """Evaluate a Moss-style filter locally."""
    if not flt:
        return True
    if "$and" in flt:
        return all(_match(meta, f) for f in flt["$and"])
    if "$or" in flt:
        return any(_match(meta, f) for f in flt["$or"])
    val = str(meta.get(flt["field"], ""))
    (op, arg), = flt["condition"].items()
    if op == "$eq":
        return val == str(arg)
    if op == "$ne":
        return val != str(arg)
    if op == "$in":
        return val in [str(a) for a in arg]
    if op == "$nin":
        return val not in [str(a) for a in arg]
    if op in ("$gt", "$gte", "$lt", "$lte"):
        try:
            a, b = float(val), float(arg)
        except ValueError:
            return False
        return {"$gt": a > b, "$gte": a >= b, "$lt": a < b, "$lte": a <= b}[op]
    return True


def f_eq(field_: str, value) -> dict:
    return {"field": field_, "condition": {"$eq": str(value)}}


def f_and(*parts) -> dict:
    parts = [p for p in parts if p]
    return {"$and": parts} if len(parts) > 1 else (parts[0] if parts else None)


class LocalIndex:
    """TF-IDF + cosine. Good enough to develop against; not a Moss substitute."""

    def __init__(self):
        self.docs: dict[str, tuple[str, dict, Counter]] = {}
        self.df: Counter = Counter()

    def add(self, docs: list[dict]):
        for d in docs:
            if d["id"] in self.docs:
                for t in set(self.docs[d["id"]][2]):
                    self.df[t] -= 1
            tf = Counter(_tok(d["text"]))
            self.docs[d["id"]] = (d["text"], d.get("metadata", {}), tf)
            for t in set(tf):
                self.df[t] += 1

    def delete(self, ids: list[str]):
        for i in ids:
            if i in self.docs:
                for t in set(self.docs[i][2]):
                    self.df[t] -= 1
                del self.docs[i]

    def query(self, q: str, top_k: int, flt: dict | None) -> list[tuple[str, str, dict, float]]:
        n = max(len(self.docs), 1)
        qtf = Counter(_tok(q))
        idf = {t: math.log(1 + n / (1 + self.df.get(t, 0))) for t in qtf}
        qv = {t: c * idf[t] for t, c in qtf.items()}
        qn = math.sqrt(sum(v * v for v in qv.values())) or 1.0
        out = []
        for did, (text, meta, tf) in self.docs.items():
            if not _match(meta, flt):
                continue
            dot = sum(qv[t] * tf.get(t, 0) * idf[t] for t in qv)
            if dot <= 0:
                continue
            dn = math.sqrt(sum((c * math.log(1 + n / (1 + self.df.get(t, 0)))) ** 2 for t, c in tf.items())) or 1.0
            out.append((did, text, meta, dot / (qn * dn)))
        out.sort(key=lambda x: -x[3])
        return out[:top_k]


_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_LOCK = threading.Lock()


def _moss_loop() -> asyncio.AbstractEventLoop:
    """One long-lived loop for the Moss client. Its connections belong to this loop, so every call must run here."""
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None:
            _LOOP = asyncio.new_event_loop()
            threading.Thread(target=_LOOP.run_forever, name="moss-io", daemon=True).start()
        return _LOOP


async def _ensure_coro(aw):
    return await aw


class _SharedMoss:
    """One Moss index for the whole deployment (plans cap the number of indexes). Every card carries its project and
    kind as metadata and every query filters on the project, so projects stay isolated inside the one index."""

    def __init__(self):
        from moss import MossClient
        self.client = MossClient(os.environ["MOSS_PROJECT_ID"], os.environ["MOSS_PROJECT_KEY"])
        self.name = os.getenv("MOSS_INDEX", "war-room")
        self.lock = threading.Lock()
        self.loaded = False
        self.exists: bool | None = None

    def run(self, value, timeout: float = 60):
        if inspect.isawaitable(value):
            return asyncio.run_coroutine_threadsafe(_ensure_coro(value), _moss_loop()).result(timeout=timeout)
        return value

    def write(self, docs: list):
        """Upsert, wait for Moss to finish building, then refresh the local copy used for queries."""
        with self.lock:
            if self.exists is None:
                try:
                    self.run(self.client.get_index(self.name), timeout=30)
                    self.exists = True
                except Exception:
                    self.exists = False
            jobs = []
            for k in range(0, len(docs), 200):
                chunk = docs[k:k + 200]
                if not self.exists:
                    r = self.run(self.client.create_index(self.name, chunk), timeout=600)
                    self.exists = True
                else:
                    r = self.run(self.client.add_docs(self.name, chunk), timeout=600)
                if getattr(r, "job_id", None):
                    jobs.append(r.job_id)
            for j in jobs:
                self.run(self.client.wait_for_job(j, timeout_seconds=600), timeout=620)
            self.load()

    def load(self):
        self.run(self.client.load_index(self.name), timeout=300)
        self.loaded = True

    def ensure_loaded(self):
        with self.lock:
            if not self.loaded:
                try:
                    self.load()
                except Exception:
                    pass  # no index yet: the first write creates it


_SHARED: _SharedMoss | None = None
_SHARED_LOCK = threading.Lock()


def _shared() -> _SharedMoss:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = _SharedMoss()
        return _SHARED


# Moss quota is per account, shared by every project: when it runs out, stop calling Moss for a while instead of
# retrying each index. Search keeps working from the local mirror.
QUOTA_BACKOFF_S = 3600
_quota = {"until": 0.0}


def _out_of_credit(e: Exception) -> bool:
    msg = str(e)
    if "credit_exhausted" in msg or "USAGE_LIMIT_EXCEEDED" in msg:
        if time.time() >= _quota["until"]:
            print(f"[memory] Moss credits are used up; searching the local index and retrying Moss in {QUOTA_BACKOFF_S // 60} min",
                  file=sys.stderr)
        _quota["until"] = time.time() + QUOTA_BACKOFF_S
        return True
    return False


class Memory:
    def __init__(self, namespace: str = "demo"):
        self.namespace = namespace
        self.stats = Stats()
        self.backend = "local"
        self.local = {name: LocalIndex() for name in INDEXES}
        self.cards: dict[str, dict] = {}  # id -> card, for expand_card and citation checks
        self._client = None
        self._syncing: set[str] = set()   # indexes still uploading to Moss; searched locally meanwhile
        self._degraded_until = 0.0        # after a Moss error, answer locally for a minute, then try Moss again
        self._lock = threading.Lock()
        pid, key = os.getenv("MOSS_PROJECT_ID"), os.getenv("MOSS_PROJECT_KEY")
        if pid and key:
            try:
                self._client = _shared()
                self.backend = "moss"
            except Exception as e:  # pragma: no cover
                print(f"[memory] Moss unavailable, using local index: {e}", file=sys.stderr)

    # ---- public API ----
    def _mirror(self, index: str, cards: list[dict]):
        """Every card also lives in the local index, so a Moss outage degrades search instead of breaking it."""
        for c in cards:
            c.setdefault("metadata", {})
            c["metadata"] = {k: (",".join(v) if isinstance(v, list) else str(v)) for k, v in c["metadata"].items()}
            c["metadata"]["index"] = index
            self.cards[c["id"]] = c
        self.local[index].add(cards)

    def _moss_ok(self, index: str) -> bool:
        return (self.backend == "moss" and self._client.loaded and index not in self._syncing
                and time.time() >= self._degraded_until and time.time() >= _quota["until"])

    def _moss_failed(self, what: str, e: Exception):
        self._degraded_until = time.time() + 60
        print(f"[memory] Moss {what} failed for {self.namespace}, using the local index for 60s: {type(e).__name__} {e}", file=sys.stderr)

    def _doc_id(self, card_id: str) -> str:
        return f"{self.namespace}::{card_id}"

    def _push(self, cards: list[dict]):
        from moss import DocumentInfo
        self._client.write([DocumentInfo(id=self._doc_id(c["id"]), text=c["text"], metadata={**c["metadata"], "project": self.namespace})
                            for c in cards])

    def add(self, index: str, cards: list[dict]):
        """New or changed cards (ingest, lessons, notes): mirror locally now, write through to Moss in the background.
        Until Moss has them, searches on this index answer from the local mirror, which already does."""
        self._mirror(index, cards)
        if self.backend == "moss" and cards:
            self._syncing.add(index)
            threading.Thread(target=self._sync, args=(index, list(cards)), daemon=True).start()

    def warm(self, index: str, cards: list[dict], fingerprint: str, synced: str | None, on_synced=None):
        """Startup: fill the local mirror now. Upload to Moss in the background only if these cards changed since the
        last sync; otherwise just make sure the shared index is loaded."""
        self._mirror(index, cards)
        if self.backend != "moss":
            return
        self._syncing.add(index)
        threading.Thread(target=self._sync, args=(index, None if fingerprint == synced else list(cards), fingerprint, on_synced),
                         daemon=True).start()

    def _sync(self, index: str, cards: list[dict] | None, fingerprint: str | None = None, on_synced=None):
        for attempt in range(5):
            if time.time() < _quota["until"]:
                self._syncing.discard(index)  # searched locally until the account has credit again
                return
            try:
                if cards:
                    self._push(cards)
                else:
                    self._client.ensure_loaded()
                self._syncing.discard(index)
                if on_synced and fingerprint:
                    on_synced(index, fingerprint)
                return
            except Exception as e:
                if _out_of_credit(e):
                    self._syncing.discard(index)
                    return
                print(f"[memory] Moss sync of {self.namespace}/{index} failed (attempt {attempt + 1}): {type(e).__name__} {e}", file=sys.stderr)
                time.sleep(min(60, 5 * 2 ** attempt))
        print(f"[memory] giving up on Moss for {self.namespace}/{index}; it stays on the local index until restart", file=sys.stderr)

    def search(self, index: str | list[str], query: str, top_k: int = 5, flt: dict | None = None,
               alpha: float | None = None) -> list[Hit]:
        names = [index] if isinstance(index, str) else index
        t0 = time.perf_counter()
        hits: list[Hit] = []
        for name in names:
            done = False
            if self._moss_ok(name):
                try:
                    from moss import QueryOptions
                    scope = [{"field": "project", "condition": {"$eq": self.namespace}}, {"field": "index", "condition": {"$eq": name}}]
                    f = {"$and": scope + ([flt] if flt else [])}
                    res = self._client.run(self._client.client.query(self._client.name, query, QueryOptions(top_k=top_k, filter=f, alpha=alpha)), timeout=10)
                    pre = self.namespace + "::"
                    hits += [Hit(d.id[len(pre):] if d.id.startswith(pre) else d.id, d.text, d.metadata or {}, d.score, name) for d in res.docs]
                    done = True
                except Exception as e:
                    if not _out_of_credit(e):
                        self._moss_failed("query", e)
            if not done:
                hits += [Hit(did, text, meta, score, name) for did, text, meta, score in self.local[name].query(query, top_k, flt)]
        hits.sort(key=lambda h: -h.score)
        self.stats.record((time.perf_counter() - t0) * 1000)
        return hits[:top_k]

    def get(self, card_id: str) -> dict | None:
        return self.cards.get(card_id)

    def status(self) -> dict:
        per_min = self.stats.count / max((time.time() - self.stats.started) / 60, 1)
        live = self.backend
        if self.backend == "moss" and time.time() < _quota["until"]:
            live = "local (Moss credits used up)"
        elif self.backend == "moss" and (self._syncing or time.time() < self._degraded_until):
            live = "moss (syncing)" if self._syncing and time.time() >= self._degraded_until else "moss (degraded, local)"
        return {"backend": live, "p50_ms": round(self.stats.p50(), 1), "searches": self.stats.count,
                "per_min": round(per_min), "indexes": len(INDEXES), "cards": len(self.cards),
                "syncing": sorted(self._syncing)}


memory = Memory("demo")  # the Lumen demo project

_registry: dict[str, Memory] = {"demo": memory}


def memory_for(project_id: str) -> Memory:
    """One isolated set of indexes per project: an agent briefed on one project can't see another."""
    if project_id not in _registry:
        _registry[project_id] = Memory(project_id)
    return _registry[project_id]
