"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Microphone, PaperPlaneRight, Phone, SidebarSimple } from "@phosphor-icons/react";
import { Approval, STATUS, State, post, usePoll } from "@/lib/api";
import { AgentMark, Button, Drawer, Initials } from "@/components/ui";
import { Focus } from "@/components/focus";

export default function WarRoom() {
  const { data: s, error } = usePoll<State>("/state", 1000);
  const [selected, setSelected] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<"needs" | "team" | null>(null);
  const [callOpen, setCallOpen] = useState<boolean | null>(null);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1280px)");
    const sync = () => setCallOpen(mq.matches);
    const t = setTimeout(sync, 0);
    mq.addEventListener("change", sync);
    return () => { clearTimeout(t); mq.removeEventListener("change", sync); };
  }, []);

  const clusters = useMemo(() => s?.clusters ?? [], [s]);
  const current = selected && clusters.some((c) => c.id === selected) ? selected : clusters[0]?.id ?? null;

  if (!s) return <Boot error={error} />;

  const onCluster = s.approvals.filter((a) => a.cluster && ["run_check", "assign", "client_update"].includes(a.kind));
  const needs = s.approvals.filter((a) => a.kind !== "decision");

  return (
    <div className="flex h-[100dvh] flex-col">
      <header className="flex h-14 shrink-0 items-center gap-4 whitespace-nowrap border-b border-line px-4 md:gap-5 md:px-5">
        <div className="flex items-center gap-3">
          <span className="flex h-[22px] w-[22px] items-center justify-center rounded-[5px] border-2 border-accent">
            <span className="h-2 w-2 rounded-[2px] bg-accent" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight">War Room OS</span>
          <span className="hidden border-l border-line-strong pl-3 text-sm text-muted md:inline">{s.project}</span>
        </div>
        {s.tickets > 0 && <span className="hidden font-mono text-sm text-text-2 sm:inline">{s.clock}</span>}
        <div className="flex-1" />
        <MemoryPill s={s} />
        <Link href="/brief" className="hidden text-sm text-muted hover:text-text md:inline">CTO brief</Link>
        <button type="button" onClick={() => setDrawer("team")} aria-label="Team and agents" className="flex -space-x-2">
          {s.people.filter((p) => p.shift === "on").slice(0, 4).map((p) => <Initials key={p.id} name={p.name} size={28} />)}
          <span className="inline-flex h-7 min-w-7 items-center justify-center rounded-full border-2 border-bg bg-raised px-1 text-[10px] text-muted">
            +{s.people.length + s.agents.length - 4}
          </span>
        </button>
        <Button variant={needs.length ? "primary" : "ghost"} size="sm" onClick={() => setDrawer("needs")}>
          Needs you <span className="font-mono">{needs.length}</span>
        </Button>
      </header>

      {s.tickets === 0 ? (
        <GoLive s={s} />
      ) : (
        <div className="flex min-h-0 flex-1">
          <nav aria-label="Clusters" className="hidden w-[260px] shrink-0 flex-col border-r border-line md:flex xl:w-[300px]">
            <div className="flex items-baseline justify-between px-5 pb-3 pt-5">
              <h2 className="text-sm font-semibold"><span className="font-mono">{s.tickets}</span> tickets, <span className="font-mono">{clusters.length}</span> clusters</h2>
              {s.sim.running && <span className="text-xs text-accent">Streaming</span>}
            </div>
            <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
              {clusters.map((c) => {
                const st = STATUS[c.status] ?? STATUS.new;
                const active = c.id === current;
                return (
                  <li key={c.id}>
                    <button type="button" onClick={() => setSelected(c.id)}
                      className={`flex w-full flex-col gap-1 rounded-lg px-3 py-3 text-left transition-colors ${active ? "bg-raised" : "hover:bg-panel"}`}>
                      <span className="flex items-center gap-2">
                        <span className="w-7 font-mono text-xs text-muted">{c.id}</span>
                        <span className="min-w-0 flex-1 truncate text-sm">{c.title}</span>
                        <span className="font-mono text-xs text-text-2">{c.count}</span>
                      </span>
                      <span className="flex items-center gap-2 pl-9 text-xs">
                        <span className={st.tone === "ok" ? "text-ok" : st.tone === "accent" ? "text-accent" : "text-muted"}>{st.label}</span>
                        {c.owner && <span className="truncate text-muted">{c.owner.split(" ")[0]}</span>}
                        {c.client_affected && <span className="text-bad">Client</span>}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>

          <main className="min-w-0 flex-1 overflow-y-auto px-5 py-6 md:px-8 md:py-8">
            <label className="mb-6 flex flex-col gap-2 text-xs text-muted md:hidden">
              Cluster
              <select value={current ?? ""} onChange={(e) => setSelected(e.target.value)}
                className="h-10 rounded-lg border border-line-strong bg-raised px-3 text-sm text-text">
                {clusters.map((c) => <option key={c.id} value={c.id}>{c.id} {c.title} ({c.count})</option>)}
              </select>
            </label>
            {current && <Focus id={current} approvals={onCluster} clusters={clusters} />}
          </main>

          {callOpen === null ? null : callOpen ? (
            <CallPanel s={s} onCollapse={() => setCallOpen(false)} />
          ) : (
            <button type="button" onClick={() => setCallOpen(true)} aria-label="Open call"
              className="flex w-12 shrink-0 flex-col items-center gap-3 border-l border-line pt-5 text-muted hover:text-text">
              <Phone size={18} />
              {s.approvals.some((a) => a.kind === "decision") && <span className="h-2 w-2 rounded-full bg-accent" />}
            </button>
          )}
        </div>
      )}

      <Drawer open={drawer === "needs"} onClose={() => setDrawer(null)} title="Needs you">
        <NeedsYou items={needs} onPick={(cid) => { setSelected(cid); setDrawer(null); }} />
      </Drawer>
      <Drawer open={drawer === "team"} onClose={() => setDrawer(null)} title="Team and agents">
        <Team s={s} />
      </Drawer>
    </div>
  );
}

function MemoryPill({ s }: { s: State }) {
  const moss = s.memory.backend === "moss";
  return (
    <span title={moss ? "Moss, in-process search" : "Local TF-IDF fallback. Add Moss keys for real semantic search."}
      className={`hidden items-center gap-2 rounded-md px-2.5 py-1 text-xs md:inline-flex ${moss ? "border border-line" : "border border-dashed border-line-strong"}`}>
      <span className="text-muted">{moss ? "Moss" : "Local index"}</span>
      <span className="font-mono text-accent">{s.memory.p50_ms}ms</span>
      <span className="font-mono text-muted">{s.memory.searches.toLocaleString()} searches</span>
    </span>
  );
}

function GoLive({ s }: { s: State }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex flex-1 items-center justify-center px-6">
      <div className="enter flex max-w-[520px] flex-col items-start gap-5">
        <h1 className="text-3xl font-semibold tracking-tight">Go live</h1>
        <p className="text-base leading-relaxed text-text-2">
          Start the migration and {s.company} streams {s.sim.total} support tickets into the room. Triage groups them by meaning as they arrive.
        </p>
        <Button variant="primary" disabled={busy} onClick={async () => { setBusy(true); await post("/sim/start", { speed: 180 }); }}>
          Start migration
        </Button>
        <p className="text-xs text-muted">Demo data. Company, people and tickets are fictional.</p>
      </div>
    </div>
  );
}

function CallPanel({ s, onCollapse }: { s: State; onCollapse: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const decisions = s.approvals.filter((a) => a.kind === "decision");

  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [s.transcript.length, decisions.length]);

  const send = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try { await post("/say", { text }); setText(""); } finally { setBusy(false); }
  };

  return (
    <aside aria-label="Call" className="flex w-[340px] shrink-0 flex-col border-l border-line">
      <div className="flex h-12 items-center gap-2 border-b border-line px-4">
        <h2 className="text-sm font-semibold">Call</h2>
        <span className="text-xs text-muted">{s.people.filter((p) => p.shift === "on").length} people, {s.agents.length} agents</span>
        <div className="flex-1" />
        <Button variant="quiet" size="sm" aria-label="Collapse call" onClick={onCollapse}><SidebarSimple size={16} /></Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {s.transcript.length === 0 && decisions.length === 0 ? (
          <p className="text-sm text-muted">Talk to the room. Try &ldquo;take cluster 1&rdquo;, &ldquo;status&rdquo;, or say a decision like &ldquo;let&apos;s skip soft-deleted users for now&rdquo;.</p>
        ) : (
          <div className="flex flex-col gap-4 text-sm leading-relaxed">
            {s.transcript.map((l) => (
              <div key={l.id} className={l.kind === "agent" ? "rounded-lg bg-raised px-3 py-2" : ""}>
                <div className={`text-[11px] ${l.kind === "agent" ? "text-accent" : "text-muted"}`}>{l.speaker}, {l.time}</div>
                <div>{l.text}</div>
              </div>
            ))}
            {decisions.map((d) => <DecisionPrompt key={d.id} a={d} />)}
            <div ref={end} />
          </div>
        )}
      </div>
      <form className="flex gap-2 border-t border-line p-3" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <label htmlFor="cmd" className="sr-only">Say something to the room</label>
        <input id="cmd" value={text} onChange={(e) => setText(e.target.value)} placeholder="Say something to the room"
          className="h-10 min-w-0 flex-1 rounded-lg border border-line-strong bg-raised px-3 text-sm text-text placeholder:text-muted focus:border-accent focus:outline-none" />
        <Button type="submit" variant="ghost" aria-label="Send" disabled={busy} className="w-10 px-0"><PaperPlaneRight size={16} /></Button>
        <Button variant="ghost" aria-label="Voice (LiveKit, not connected yet)" title="Voice via LiveKit is next" disabled className="w-10 px-0"><Microphone size={16} /></Button>
      </form>
    </aside>
  );
}

function DecisionPrompt({ a }: { a: Approval }) {
  const [busy, setBusy] = useState(false);
  const act = async (action: string) => { setBusy(true); try { await post(`/approvals/${a.id}`, { action }); } finally { setBusy(false); } };
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed border-accent px-3 py-3">
      <p className="text-sm">{a.title}</p>
      <p className="text-xs text-muted">&ldquo;{a.detail}&rdquo;</p>
      <div className="flex gap-2">
        <Button size="sm" variant="primary" disabled={busy} onClick={() => act("approve")}>Pin as decision</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => act("dismiss")}>Not a decision</Button>
      </div>
    </div>
  );
}

function NeedsYou({ items, onPick }: { items: Approval[]; onPick: (cid: string) => void }) {
  if (!items.length) return <p className="text-sm text-muted">Nothing is waiting on you. Agents will ask here before anything risky.</p>;
  return (
    <ul className="flex flex-col gap-2">
      {items.map((a) => (
        <li key={a.id}>
          <button type="button" onClick={() => a.cluster && onPick(a.cluster)}
            className="flex w-full items-start gap-3 rounded-lg border border-line px-3 py-3 text-left hover:bg-raised">
            <span className="mt-0.5 font-mono text-[10px] text-accent">{a.agent === "dispatcher" ? "DP" : a.agent === "root-cause" ? "RC" : "CA"}</span>
            <span className="flex-1 text-sm">{a.title}</span>
            {a.cluster && <span className="font-mono text-xs text-muted">{a.cluster}</span>}
          </button>
        </li>
      ))}
    </ul>
  );
}

function Team({ s }: { s: State }) {
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h3 className="text-xs font-semibold text-muted">People, hours against the 12h cap</h3>
        {s.people.map((p) => (
          <div key={p.id} className="flex items-center gap-3">
            <Initials name={p.name} dim={p.shift !== "on"} size={32} />
            <div className="min-w-0 flex-1">
              <div className="flex justify-between text-sm">
                <span className={p.shift === "on" ? "" : "text-muted"}>{p.name}</span>
                <span className={`font-mono text-xs ${p.over_cap ? "text-bad" : "text-muted"}`}>{p.hours}h</span>
              </div>
              <div className="truncate text-xs text-muted">{p.role}{p.shift !== "on" ? ", off shift" : p.doing ? `, ${p.doing}` : ""}</div>
            </div>
          </div>
        ))}
      </section>
      <section className="flex flex-col gap-3 border-t border-line pt-5">
        <h3 className="text-xs font-semibold text-muted">Agents, all propose before acting</h3>
        {s.agents.map((a) => (
          <div key={a.id} className="flex items-center gap-3">
            <AgentMark short={a.short} active={a.activity !== "Idle"} />
            <span className="text-sm">{a.name}</span>
            <span className="flex-1 truncate text-right text-xs text-muted">{a.activity}</span>
          </div>
        ))}
      </section>
      <p className="border-t border-line pt-4 text-xs text-muted">
        Model: {s.model.provider}, {s.model.online ? "online" : "offline, theories come from playbooks"}. {s.model.used.toLocaleString()} of {s.model.budget.toLocaleString()} tokens used, {s.model.cached} cached.
      </p>
    </div>
  );
}

function Boot({ error }: { error: string | null }) {
  return (
    <div className="flex h-[100dvh] items-center justify-center px-6 text-sm text-muted">
      {error ? "Can't reach the War Room API on port 8010. Start it with: uvicorn backend.app:app --port 8010" : "Loading the war room"}
    </div>
  );
}
