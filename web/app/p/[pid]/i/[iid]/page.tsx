"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { Robot, ShieldCheck } from "@phosphor-icons/react";
import { post, usePoll } from "@/lib/api";
import { Shell, inputCls } from "@/components/shell";
import { Badge, Button, Initials } from "@/components/ui";
import { DiagramData, FlowDiagram } from "@/components/diagram";
import { Attachment, Evidence } from "@/components/evidence";
import { Note, Thread } from "@/components/thread";

type Step = { step: string; actor: string; kind: string; note: string; at: number };
type Room = {
  incident: { id: string; title: string; severity: string; status: string; trigger: string; rule: string; opened_at: number;
    owner: string; owner_id: string | null; relay: Step[]; opened_by: string };
  role: string; me: string; project: string; threshold: number | null;
  evidence: { query?: string; value?: number; baseline?: number | null; calls?: number; notes?: string;
    context?: { tables: string[]; filter_columns: string[]; code: string[]; prs: string[]; hints: string[] };
    proposals?: { action: string; detail: string; risk: string }[] };
  series: { at: number; value: number }[]; diagram: DiagramData | null;
  thread: Note[]; files: Attachment[]; proposal_why: string | null;
};

const STAGES = ["detected", "diagnosed", "owner", "mitigating", "resolved"];
const LABEL: Record<string, string> = { detected: "Detected", diagnosed: "Diagnosed", owner: "Owner", mitigating: "Mitigating", resolved: "Resolved" };
const WHO: Record<string, string> = { detected: "Monitoring agent or a person", diagnosed: "Root cause agent", owner: "Dispatcher, then a person", mitigating: "A person approves", resolved: "A person" };

export default function IncidentRoom() {
  const { pid, iid } = useParams<{ pid: string; iid: string }>();
  const { data: r, error, reload } = usePoll<Room>(`/incidents/${iid}`, 2000);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (!r) return <Shell crumbs={[{ label: "War room" }]}><p className="text-sm text-muted">{error ?? "Loading"}</p></Shell>;
  const i = r.incident;
  const lead = r.role === "owner" || r.role === "lead";
  const act = async (action: string, detail = "") => {
    setBusy(true); setErr(null);
    try { await post(`/incidents/${iid}/act`, { action, detail }); reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };
  const done = Object.fromEntries(i.relay.map((s) => [s.step, s]));
  const at = Math.max(-1, ...i.relay.map((s) => STAGES.indexOf(s.step)));

  return (
    <Shell wide crumbs={[{ label: "Project", href: `/p/${pid}` }, { label: "War room" }]}>
      <div className="flex flex-col gap-10">
        <header className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <Badge tone={i.severity === "high" ? "bad" : "accent"}>{i.severity}</Badge>
            <Badge tone={i.status === "resolved" ? "ok" : i.status === "proposed" ? "accent" : "bad"}>{i.status}</Badge>
            <span className="flex items-center gap-1.5">{i.trigger === "agent" ? <><Robot size={13} className="text-agent" />Raised by the monitoring agent</> : `Opened by ${i.opened_by}`}</span>
            <span>{new Date(i.opened_at * 1000).toLocaleString()}</span>
          </div>
          <h1 className="max-w-[56ch] text-2xl font-semibold leading-snug tracking-tight">{i.title}</h1>
        </header>

        {i.status === "proposed" && (
          <section className="flex flex-wrap items-center gap-3 rounded-xl border border-accent/50 bg-accent-soft/40 px-5 py-4">
            <ShieldCheck size={20} className="text-accent" />
            <div className="min-w-[240px] flex-1 text-sm">
              <p className="font-medium">The monitoring agent wants to open a war room</p>
              <p className="text-text-2">Your guardrails say a person decides for this severity. Open it and agents start diagnosing, or dismiss it and the agent stays quiet about this signal during its cooldown.</p>
            </div>
            <Button variant="primary" disabled={busy} onClick={() => act("confirm")}>Open war room</Button>
            <Button variant="quiet" disabled={busy} onClick={() => act("dismiss", "not an incident")}>Not an incident</Button>
          </section>
        )}

        <section className="rounded-xl border border-line p-5">
          <ol className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            {STAGES.map((s, idx) => {
              const d = done[s];
              return (
                <li key={s} className="flex min-w-0 flex-col gap-2">
                  <div className="flex items-center gap-2">
                    {d ? (d.kind === "agent"
                      ? <span className="flex h-7 w-7 items-center justify-center rounded-full bg-agent-soft text-agent"><Robot size={15} /></span>
                      : <Initials name={d.actor} size={28} />)
                      : <span className={`h-7 w-7 rounded-full border border-dashed ${idx === at + 1 ? "border-accent" : "border-line-strong"}`} />}
                    <span className={`h-px flex-1 ${idx < at ? "bg-text-2" : "bg-line-strong"} ${idx === STAGES.length - 1 ? "invisible" : ""}`} />
                  </div>
                  <p className={`text-xs font-medium ${d ? "" : "text-muted"}`}>{LABEL[s]}</p>
                  <p className="text-[11px] text-muted">{d ? d.actor : WHO[s]}</p>
                  {d && <p className="line-clamp-4 text-[11px] leading-snug text-text-2">{d.note}</p>}
                </li>
              );
            })}
          </ol>
        </section>

        {r.series.length > 1 && <Signal r={r} />}

        {r.evidence.query && (
          <section className="flex flex-col gap-2">
            <h2 className="text-sm font-semibold">The query</h2>
            <pre className="overflow-x-auto rounded-lg border border-line bg-panel p-3 font-mono text-[12px] text-text-2">{r.evidence.query}</pre>
          </section>
        )}

        {r.diagram && (
          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold">How it connects</h2>
            <p className="text-xs text-muted">From the slow query to the table, the code that runs it, the changes that last touched that code, and who made them.</p>
            <div className="rounded-xl border border-line p-4"><FlowDiagram data={r.diagram} /></div>
          </section>
        )}

        {i.status !== "proposed" && (
          <div className="grid gap-10 lg:grid-cols-2">
            <section className="flex flex-col gap-4">
              <h2 className="text-sm font-semibold">What to do</h2>
              {(r.evidence.context?.hints ?? []).map((h) => <p key={h} className="text-sm text-text-2">{h}</p>)}
              <ul className="flex flex-col gap-2">
                {(r.evidence.proposals ?? []).map((p) => (
                  <li key={p.detail} className="flex flex-wrap items-center gap-3 rounded-lg border border-line px-4 py-3">
                    <Badge tone={p.risk === "high" ? "bad" : p.risk === "medium" ? "accent" : "ok"}>{p.risk} risk</Badge>
                    <code className="min-w-0 flex-1 break-words font-mono text-[12px]">{p.detail}</code>
                    {(lead || r.me === i.owner_id) && i.status !== "resolved" &&
                      <Button size="sm" disabled={busy} onClick={() => act("approve_mitigation", p.detail)}>Approve</Button>}
                  </li>
                ))}
              </ul>
              {!(r.evidence.proposals ?? []).length && <p className="text-sm text-muted">No mitigation proposed yet.</p>}
              <p className="text-[11px] text-muted">Agents propose, people approve and run. Nothing here executes on its own.</p>
              <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
                <span className="text-sm text-text-2">Owner: {i.owner || "nobody yet"}</span>
                {r.me !== i.owner_id && i.status !== "resolved" && <Button size="sm" disabled={busy} onClick={() => act("take")}>I&apos;ll take it</Button>}
              </div>
              {i.status !== "resolved" && (
                <div className="flex gap-2">
                  <label htmlFor="res" className="sr-only">Resolution</label>
                  <input id="res" className={`${inputCls} flex-1`} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What fixed it" />
                  <Button variant="primary" disabled={busy} onClick={() => act("resolve", note)}>Resolve</Button>
                </div>
              )}
              {err && <p className="text-xs text-bad">{err}</p>}
            </section>
            <Thread ownerType="incident" ownerId={iid} notes={r.thread} canWrite={r.role !== "client_guest" && r.role !== "viewer"} onChange={reload} />
          </div>
        )}
        {i.status !== "proposed" && <Evidence ownerType="incident" ownerId={iid} items={r.files} canAdd={r.role !== "client_guest"} onChange={reload} />}
      </div>
    </Shell>
  );
}

/** The signal that crossed the guardrail: value over time, baseline and threshold. */
function Signal({ r }: { r: Room }) {
  const pts = r.series;
  const W = 720, H = 160, P = 28;
  const max = Math.max(...pts.map((p) => p.value), r.threshold ?? 0, (r.evidence.baseline ?? 0)) * 1.15 || 1;
  const t0 = pts[0].at, t1 = pts[pts.length - 1].at || t0 + 1;
  const x = (t: number) => P + ((t - t0) / Math.max(t1 - t0, 1)) * (W - 2 * P);
  const y = (v: number) => H - P - (v / max) * (H - 2 * P);
  const line = pts.map((p, k) => `${k ? "L" : "M"}${x(p.at)},${y(p.value)}`).join(" ");
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold">What the agent saw</h2>
      <div className="overflow-x-auto rounded-xl border border-line p-4">
        <svg width={W} height={H} role="img" aria-label="Query time over the incident window">
          {r.threshold != null && <>
            <line x1={P} x2={W - P} y1={y(r.threshold)} y2={y(r.threshold)} stroke="var(--bad)" strokeDasharray="4 4" />
            <text x={W - P} y={y(r.threshold) - 5} textAnchor="end" fontSize="10" fill="var(--bad)">threshold {r.threshold} ms</text>
          </>}
          {r.evidence.baseline != null && <>
            <line x1={P} x2={W - P} y1={y(r.evidence.baseline)} y2={y(r.evidence.baseline)} stroke="var(--ok)" strokeDasharray="2 4" />
            <text x={P} y={y(r.evidence.baseline) - 5} fontSize="10" fill="var(--ok)">baseline {r.evidence.baseline} ms</text>
          </>}
          <path d={line} fill="none" stroke="var(--accent)" strokeWidth="2" />
          {pts.map((p, k) => <circle key={k} cx={x(p.at)} cy={y(p.value)} r="3" fill="var(--accent)"><title>{p.value.toFixed(2)} ms at {new Date(p.at * 1000).toLocaleTimeString()}</title></circle>)}
          <text x={P} y={H - 6} fontSize="10" fill="var(--muted)">{new Date(t0 * 1000).toLocaleTimeString()}</text>
          <text x={W - P} y={H - 6} textAnchor="end" fontSize="10" fill="var(--muted)">{new Date(t1 * 1000).toLocaleTimeString()}</text>
        </svg>
      </div>
    </section>
  );
}
