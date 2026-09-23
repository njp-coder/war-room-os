"""Triage agent: group tickets by meaning as they arrive. Code + Moss, no model."""
from __future__ import annotations

import re
from collections import Counter

from .memory import memory, f_eq
from .store import store, Cluster

JOIN_THRESHOLD = 0.22
_STOP = {"please", "help", "asap", "urgent", "this", "is", "the", "my", "i", "a", "on", "since", "yesterday", "app",
         "third", "time", "reporting", "for", "to", "after", "your", "update", "but"}


def _clean(text: str) -> str:
    return re.sub(r"\s+(asap|please help|this is urgent|third time reporting|on the app|since yesterday)$", "", text).strip()


def _title(texts: list[str]) -> str:
    """Most typical phrasing among members (highest word overlap with the rest)."""
    cleaned = [_clean(t) for t in texts[-40:]]
    words = Counter(w for t in cleaned for w in set(t.lower().split()) if w not in _STOP)
    best = max(cleaned, key=lambda t: sum(words[w] for w in set(t.lower().split()) if w not in _STOP) / (len(t.split()) ** 0.5))
    return best[0].upper() + best[1:]


def _signature(text: str) -> str | None:
    """Nearest playbook. A second signal so paraphrases of one failure land together."""
    hits = memory.search("knowledge", text, top_k=1, flt=f_eq("type", "skill"))
    return hits[0].id if hits and hits[0].score > 0.08 else None


def triage(ticket: dict, world) -> Cluster:
    with store.lock:
        store.tickets[ticket["id"]] = ticket
        sig = _signature(ticket["text"])
        hits = memory.search("signals", ticket["text"], top_k=5, flt=f_eq("type", "cluster"), alpha=0.85)
        cluster = None
        if memory.backend == "local" and sig:
            # Lexical fallback can't see paraphrases, so the playbook signature leads.
            cluster = next((c for c in store.clusters.values() if c.signature == sig), None)
        else:
            # Moss: meaning leads, the signature only guards against merging different failures.
            for h in hits:
                cand = store.clusters.get(h.metadata.get("cluster"))
                if not cand:
                    continue
                if h.score >= JOIN_THRESHOLD and (sig is None or cand.signature in (None, sig)):
                    cluster = cand
                    break
        if cluster is None:
            cid = f"C{len(store.clusters) + 1}"
            cluster = Cluster(id=cid, title=_clean(ticket["text"]).capitalize(), symptom="", service="", fields=[],
                              first_seen=ticket["minute"], signature=sig)
            store.clusters[cid] = cluster
            store.emit("triage", "cluster_created", {"cluster": cid, "ticket": ticket["id"]})
        cluster.ticket_ids.append(ticket["id"])
        cluster.user_ids.append(ticket["user_id"])
        if ticket.get("client") and ticket["client"] not in cluster.clients:
            cluster.clients.append(ticket["client"])
            cluster.client_affected = True
        member_texts = [store.tickets[t]["text"] for t in cluster.ticket_ids]
        if len(cluster.ticket_ids) in (1, 2, 4, 8, 16, 32, 64):
            cluster.title = _title(member_texts)
        memory.add("signals", [{"id": f"cluster:{cluster.id}", "text": " | ".join(_clean(t) for t in member_texts[-30:]),
                                "metadata": {"type": "cluster", "cluster": cluster.id}}])
        memory.add("signals", [{"id": f"ticket:{ticket['id']}", "text": ticket["text"],
                                "metadata": {"type": "ticket", "cluster": cluster.id, "minute": ticket["minute"]}}])
        store.activity("triage", f"Grouped {len(store.tickets)} tickets into {len(store.clusters)} clusters")
        return cluster
