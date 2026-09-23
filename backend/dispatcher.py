"""Dispatcher: routes clusters to people. Code only, hard rules enforced here.

Ranking: built or reviewed the change > owns the code > handled it before > load.
Constraints (never overridden by ranking):
  - off shift or over the hours cap: no new work, schedule a handover instead
  - client updates go only to the account owner (client-facing)
"""
from __future__ import annotations

from .memory import memory, f_eq
from .store import store, Approval

HOURS_CAP = 12


def _owners(world, cluster) -> set[str]:
    owners = set()
    for line in world.d["codeowners"].splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        path, *who = line.split()
        area = path.strip("/")
        if cluster.service and (area.startswith(cluster.service) or cluster.service.startswith(area)):
            owners |= {w.lstrip("@") for w in who}
    return owners


def _load(person_id: str) -> int:
    return sum(1 for c in store.clusters.values() if c.owner == person_id and c.status not in ("resolved",))


def route(cluster_id: str, world):
    cluster = store.clusters[cluster_id]
    if cluster.owner:
        return None
    people = world.people
    change = next((s for s in cluster.evidence if s["name"] == "What changed"), None)
    commit_card = memory.get(change["cites"][0]) if change and change["cites"] else None
    author = commit_card["metadata"].get("author") if commit_card else None
    reviewer = commit_card["metadata"].get("reviewer") if commit_card else None
    owners = _owners(world, cluster)
    texts = " ".join(store.tickets[t]["text"] for t in cluster.ticket_ids[-10:]) + " " + cluster.service
    handled = {h.metadata["person"]: h.score for h in memory.search("people", texts, top_k=6)}

    scored = []
    for pid, p in people.items():
        if pid in ("arjun", "you") or p["role"].startswith("Account") or p["role"].startswith("Support"):
            continue
        reasons, score = [], 0.0
        if pid == author:
            score += 4; reasons.append(f"wrote PR #{commit_card['metadata']['pr']}")
        if pid == reviewer:
            score += 3; reasons.append(f"reviewed PR #{commit_card['metadata']['pr']}")
        if pid in owners:
            score += 2; reasons.append(f"owns {cluster.service}")
        if handled.get(pid, 0) > 0.05:
            score += 1 + handled[pid]; reasons.append("handled similar before")
        if not reasons:
            continue
        load = _load(pid)
        score -= 0.5 * load
        scored.append((score, pid, reasons, load))
    scored.sort(reverse=True)
    if not scored:
        return store.add_approval(Approval(id=f"ap-assign-{cluster.id}", kind="assign", agent="dispatcher", cluster=cluster.id,
                                           title=f"{cluster.id}: nobody with history here, pick someone", needs="lead"))

    available = [(s, pid, r, l) for s, pid, r, l in scored
                 if people[pid]["shift"] == "on" and people[pid]["hours_today"] < HOURS_CAP]
    best_any = scored[0]
    pick = available[0] if available else None
    note = ""
    if best_any[1] != (pick[1] if pick else None):
        p = people[best_any[1]]
        why = "off shift" if p["shift"] != "on" else f"{p['hours_today']}h today, over the {HOURS_CAP}h cap"
        note = f"{p['name'].split()[0]} knows it best but is {why}; handover at 09:00."
    if not pick:
        return store.add_approval(Approval(id=f"ap-assign-{cluster.id}", kind="assign", agent="dispatcher", cluster=cluster.id,
                                           title=f"{cluster.id}: no one available, {note}", needs="lead"))
    _, pid, reasons, load = pick
    name = people[pid]["name"].split()[0]
    title = f"{name} takes {cluster.id} now" + (f", {people[best_any[1]]['name'].split()[0]} at 09:00" if note else "")
    needs = "lead"
    appr = store.add_approval(Approval(id=f"ap-assign-{cluster.id}", kind="assign", agent="dispatcher", cluster=cluster.id,
                                       title=title, detail=f"{', '.join(reasons)}. {load} open items. {note}".strip(),
                                       payload={"person": pid, "handover": best_any[1] if note else None}, needs=needs))
    store.activity("dispatcher", f"Routing {cluster.id}")
    if cluster.client_affected:
        draft_client_update(cluster, world)
    return appr


def draft_client_update(cluster, world):
    for client in cluster.clients:
        owner_id = next(c["owner"] for c in world.d["clients"] if c["name"] == client)
        owner = world.people[owner_id]
        if not owner["client_facing"]:
            continue  # hard rule: never route client comms to someone who isn't client-facing
        members = sum(1 for t in cluster.ticket_ids if store.tickets[t].get("client") == client)
        text = (f"Hi {client} team, {members} of your members reported an issue after our platform migration "
                f"(for example: \"{cluster.title}\"). We've found the cause and a fix is in progress. "
                f"We'll confirm next steps, including any refunds, in our next update.")
        store.add_approval(Approval(id=f"ap-client-{cluster.id}-{client.split()[0].lower()}", kind="client_update", agent="dispatcher", cluster=cluster.id,
                                    title=f"Client update for {cluster.id}, to {owner['name'].split()[0]}",
                                    detail=text, payload={"client": client, "owner": owner_id}, needs="account_owner"))


def accept(approval: Approval):
    cluster = store.clusters[approval.cluster]
    pid = approval.payload.get("person")
    if pid:
        cluster.owner = pid
        cluster.assignment = {"person": pid, "handover": approval.payload.get("handover")}
        store.emit("dispatcher", "assigned", {"cluster": cluster.id, "person": pid})
