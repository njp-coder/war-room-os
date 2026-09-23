"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { Check, Play, Robot } from "@phosphor-icons/react";
import { post, usePoll } from "@/lib/api";
import { Shell, inputCls } from "@/components/shell";
import { Button, Initials } from "@/components/ui";
import { DiagramData, FlowDiagram } from "@/components/diagram";
import type { Card } from "@/components/board";
import { Attachment, Evidence } from "@/components/evidence";
import { Note, Thread } from "@/components/thread";
import Link from "next/link";
import { AgentTrace, Run } from "@/components/trace";

type Advice = { id: string; expert: string; text: string; signoff: string | null; signoff_name: string | null; verdict: string; learned_from: string[]; lessons: number };
type Room = {
  bug: Card; body: string; severity: string; role: string; me: string; suggested: string[];
  evidence: { steps: { name: string; summary: string; cites: string[] }[]; theory: { text: string; confidence: number } | null; source?: string };
  repro: { steps: { do: string; detail: string }[]; script: string; run: { kind: string } | null;
    result: { ok: boolean; summary: string; ms: number; table: { columns: string[]; rows: unknown[][] } | null } | null };
  diagram: DiagramData | null;
  proposal: { user: string | null; why: string; pair?: string; alternates: { user: string; name: string; why: string; capacity: number }[] } | null;
  eta_agent_why: string | null;
  people: { id: string; name: string; left: number; shift: string }[];
  runs: Run[];
  advice: Advice[];
  files: Attachment[]; thread: Note[]; help_test: { id: string; claimed_by: string | null; status: string } | null;
};

const STEPS = [
  { s: "reported", label: "Reported" }, { s: "reproduced", label: "Reproduced" }, { s: "cause_found", label: "Cause found" },
  { s: "proposed", label: "Needs an owner" }, { s: "fixing", label: "Fixing" }, { s: "fixed", label: "Needs a check" }, { s: "verified", label: "Verified" },
];
const WORKING: Record<string, string> = {
  reported: "The repro agent is trying it on staging.", reproduced: "Reproduced. The root-cause agent is tracing it through the code and history.",
  cause_found: "Cause found. The dispatcher is picking who should fix it.",
};

export default function BugRoom() {
  const { pid, rid, bid } = useParams<{ pid: string; rid: string; bid: string }>();
  const { data: r, error, reload } = usePoll<Room>(`/bugs/${bid}/room`, 1500);
  const [open, setOpen] = useState<"repro" | "how" | null>(null);
  if (!r) return <Shell crumbs={[{ label: "Bug" }]}><div className="flex flex-col gap-4"><div className="h-5 w-96 animate-pulse rounded bg-panel" /><div className="h-10 w-[560px] max-w-full animate-pulse rounded-lg bg-panel" />{error && <p className="text-sm text-muted">{error}</p>}</div></Shell>;
  const b = r.bug;
  const reached = new Set(b.relay.map((x) => x.step));
  const working = ["reported", "reproduced", "cause_found"].includes(b.stage);
  const t = r.evidence.theory;
  const cause = t ? firstSentences(t.text) : WORKING[b.stage] ?? "";
  const seen = r.evidence.steps.find((s) => /seen|precedent/i.test(s.name));
  const res = r.repro.result;

  return (
    <Shell wide crumbs={[{ label: "Release", href: `/p/${pid}/r/${rid}` }, { label: b.title.slice(0, 40) }]}>
      <div className="flex flex-col gap-9 md:px-6">
        <StageLine stage={b.stage} reached={reached} ask={needsMe(r)} />

        <section className="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_420px]">
          <div className="flex flex-col gap-3">
            <h1 className="max-w-[28ch] text-[30px] font-semibold leading-tight tracking-tight">{b.title}</h1>
            <p key={cause} className="arrive max-w-[62ch] text-base leading-relaxed text-[#b8b7b2]">
              {cause} {b.reporter_kind === "agent" ? `Found by the agent tester on ${b.env}.` : `Reported by ${b.reporter} on ${b.env}.`}
            </p>
            {(b.blocker || b.priority === "P0") && <p className="text-[13px] text-bad">{b.blocker ? "Blocks the release." : ""} {b.priority === "P0" ? "P0." : ""}</p>}
          </div>
          <NextAction r={r} reload={reload} />
        </section>

        <ExpertReview r={r} bid={bid} reload={reload} />

        {r.diagram && (r.diagram.nodes.length > 1 || working) && (
          <section className="flex flex-col gap-3">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="text-[15px] font-semibold">How it connects</h2>
              <span className="text-xs text-muted">{working ? "Lights up as the agents confirm each piece" : "Bright path is the agents' best guess"}</span>
            </div>
            <div className="rounded-2xl border border-line p-5"><FlowDiagram data={r.diagram} reached={reached} working={working} /></div>
          </section>
        )}

        {r.help_test && (
          <section className="flex flex-wrap items-center gap-3 rounded-2xl border border-line px-5 py-3 text-sm">
            <Robot size={16} className="text-agent" />
            <span className="flex-1">The repro agent couldn&apos;t reproduce this alone and asked a tester to try it and record it.</span>
            <Link href={`/p/${pid}/r/${rid}/t/${r.help_test.id}`} className="text-text-2 underline-offset-2 hover:underline">Open the check ({r.help_test.status === "todo" ? "waiting" : r.help_test.status})</Link>
          </section>
        )}

        <section className="grid gap-3.5 md:grid-cols-3">
          <Small title={res ? (res.ok ? "Reproduced on staging" : "Didn't reproduce") : "Reproduce it"}
            body={res ? res.summary : r.repro.steps.length ? `${r.repro.steps.length} steps written` : "The repro agent is working on it."}
            link={r.repro.steps.length || res ? { label: open === "repro" ? "Hide the query" : "See the query and rows", on: () => setOpen(open === "repro" ? null : "repro") } : undefined} />
          <Small title="How sure" body={t ? <><span className="font-mono text-text">{t.confidence}%</span>{r.evidence.steps.length ? `, from ${r.evidence.steps.length} checks` : ""}</> : "No theory yet."}
            link={t || r.runs.length ? { label: open === "how" ? "Hide" : "How the agents got there", on: () => setOpen(open === "how" ? null : "how") } : undefined} />
          <Small title="Seen before" body={seen ? firstSentences(seen.summary, 1) : "No earlier fix matches."}
            foot={r.suggested[0] ? `Agent suggests ${r.suggested[0]}${r.suggested[1] ? `: ${r.suggested[1]}` : ""}` : undefined} />
        </section>

        {open === "repro" && <div className="enter"><Repro r={r} bid={bid} reload={reload} /></div>}
        {open === "how" && <div className="enter flex flex-col gap-8"><Cause r={r} /><AgentTrace runs={r.runs} /></div>}

        <div className="grid gap-10 border-t border-line pt-9 lg:grid-cols-2">
          <Thread ownerType="bug" ownerId={bid} notes={r.thread} canWrite={["owner", "lead", "engineer", "tester", "support"].includes(r.role)} onChange={reload} />
          <Evidence ownerType="bug" ownerId={bid} items={r.files} canAdd={["owner", "lead", "engineer", "tester", "support"].includes(r.role)} onChange={reload} />
        </div>
      </div>
    </Shell>
  );
}

/**
 * The area's expert, standing in for seniors who aren't here. Grey by default: amber only when the
 * person looking is the senior asked to sign off.
 */
function ExpertReview({ r, bid, reload }: { r: Room; bid: string; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const a = r.advice[0];
  const canWrite = ["owner", "lead", "engineer", "tester", "support"].includes(r.role);
  const ask = async () => {
    setBusy(true); setErr(null);
    try { await post(`/bugs/${bid}/expert`); reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };
  const say = async (verdict: string) => { await post(`/advice/${a.id}/verdict`, { verdict }); reload(); };
  if (!a) {
    if (!canWrite || ["reported", "reproduced", "cause_found"].includes(r.bug.stage)) return null;
    return (
      <div className="flex flex-wrap items-center gap-3 text-[13px] text-muted">
        <button type="button" disabled={busy} onClick={ask} className="text-text-2 underline-offset-2 hover:underline disabled:opacity-50">{busy ? "Asking" : "Ask the expert for this area"}</button>
        <span>for a second opinion from what seniors wrote here before.</span>
        {err && <span className="text-bad">{err}</span>}
      </div>
    );
  }
  const mine = a.signoff === r.me && a.verdict !== "signed_off";
  return (
    <section className={`flex flex-col gap-3 rounded-2xl border px-5 py-4 ${mine ? "border-[#4a3a1e] bg-[#1a1712]" : "border-line"}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <span className="flex items-center gap-2 text-sm font-medium"><Robot size={15} className="text-agent" />{a.expert}</span>
        <span className="text-xs text-muted">{a.learned_from.length ? `learned from ${a.learned_from.join(", ")}` : "no teachers yet"}{a.lessons ? `, used ${a.lessons} lessons` : ""}</span>
      </div>
      <p className="max-w-[90ch] text-sm leading-relaxed text-text-2">{a.text}</p>
      <div className="flex flex-wrap items-center gap-2">
        {mine && <button type="button" onClick={() => say("signed_off")} className="inline-flex h-9 items-center rounded-lg bg-accent px-3.5 text-[13px] font-semibold text-accent-ink">Sign off</button>}
        {a.verdict === "signed_off" && <span className="flex items-center gap-1.5 text-xs text-ok"><Check size={12} />Signed off</span>}
        {a.signoff_name && a.verdict !== "signed_off" && !mine && <span className="text-xs text-muted">Waiting on {a.signoff_name} to sign off.</span>}
        {canWrite && a.verdict === "pending" && <>
          <button type="button" onClick={() => say("followed")} className="h-8 rounded-lg border border-line-strong px-3 text-xs hover:bg-raised">Following this</button>
          <button type="button" onClick={() => say("not_useful")} className="h-8 px-2 text-xs text-muted hover:text-text">Not useful</button>
        </>}
        {(a.verdict === "followed" || a.verdict === "held") && <span className="text-xs text-muted">{a.verdict === "held" ? "Followed, and the fix held." : "Being followed. Counts for the expert once the fix is verified."}</span>}
        {a.verdict === "not_useful" && <span className="text-xs text-muted">Marked not useful. Reply in the thread to teach it better.</span>}
      </div>
    </section>
  );
}

function firstSentences(text: string, n = 2) {
  // Split only where a sentence really ends: punctuation, space, capital. Keeps "models.py" whole.
  const parts = text.replace(/\s+/g, " ").trim().split(/(?<=[.!?])\s+(?=[A-Z#])/);
  return parts.slice(0, n).join(" ");
}

/** Whether the person looking at this page is the one who has to act next. */
function needsMe(r: Room) {
  const lead = r.role === "owner" || r.role === "lead";
  const b = r.bug;
  if (b.stage === "proposed") return lead || b.who_id === r.me;
  if (b.stage === "fixing") return b.who_id === r.me;
  if (b.stage === "fixed") return lead || r.role === "tester";
  return false;
}

function StageLine({ stage, reached, ask }: { stage: string; reached: Set<string>; ask: boolean }) {
  const at = STEPS.findIndex((x) => x.s === stage);
  return (
    <ol className="flex flex-wrap items-center gap-x-2.5 gap-y-2 text-[13px]" aria-label="Where this bug is">
      {STEPS.map((x, i) => {
        const done = i < at || (i === at && x.s === "verified");
        const current = i === at && x.s !== "verified";
        const agentStep = ["reported", "reproduced", "cause_found"].includes(x.s);
        return (
          <li key={x.s} className="flex items-center gap-2.5" aria-current={current ? "step" : undefined}>
            <span className={done ? (x.s === "verified" ? "text-ok" : "text-text-2") : current ? (ask ? "font-semibold text-accent" : "font-semibold text-text") : "text-faint"}>
              {done && reached.has(x.s) && <Check size={11} className="mr-1 inline" />}{current && agentStep ? `${x.label}, agents working` : x.label}
            </span>
            {i < STEPS.length - 1 && <span className={`h-px w-6 ${i < at ? "bg-line-strong" : "bg-line"}`} />}
          </li>
        );
      })}
    </ol>
  );
}

function Small({ title, body, link, foot }: { title: string; body: React.ReactNode; link?: { label: string; on: () => void }; foot?: string }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-2xl border border-line px-5 py-4">
      <span className="text-[13px] font-medium">{title}</span>
      <span className="line-clamp-3 text-[13px] text-muted">{body}</span>
      {foot && <span className="text-xs text-faint">{foot}</span>}
      {link && <button type="button" onClick={link.on} className="self-start text-[13px] text-text-2 underline-offset-2 hover:underline">{link.label}</button>}
    </div>
  );
}

function NextAction({ r, reload }: { r: Room; reload: () => void }) {
  const b = r.bug;
  const [eta, setEta] = useState<string>("");
  const [to, setTo] = useState("");
  const [moving, setMoving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const lead = r.role === "owner" || r.role === "lead";
  const act = async (path: string, body: unknown = {}) => {
    setBusy(true); setErr(null);
    try { await post(`/bugs/${b.id}/${path}`, body); setMoving(false); reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };
  const proposedMe = b.who_id === r.me;
  const ask = needsMe(r);
  const card = `flex flex-col gap-4 rounded-2xl border px-5 py-5 ${ask ? "border-[#4a3a1e] bg-[#1a1712]" : "border-line"}`;
  const primary = "inline-flex h-10 items-center rounded-lg bg-accent px-4 text-[13px] font-semibold text-accent-ink disabled:opacity-50 active:scale-[0.98]";
  const secondary = "inline-flex h-10 items-center rounded-lg border border-line-strong px-3.5 text-[13px] hover:bg-raised disabled:opacity-50";

  if (["reported", "reproduced", "cause_found"].includes(b.stage)) {
    return (
      <div className={card}>
        <span className="flex items-center gap-2 text-sm"><Robot size={16} className="animate-pulse text-agent" />Agents are on it</span>
        <span className="text-[13px] text-muted">{WORKING[b.stage]} Nothing needed from you yet.</span>
      </div>
    );
  }
  if (b.stage === "proposed") {
    const p = r.proposal;
    const free = r.people.find((x) => x.id === b.who_id)?.left;
    return (
      <div className={card}>
        <div className="flex items-center gap-3">
          {b.who ? <Initials name={b.who} size={40} /> : null}
          <div className="flex min-w-0 flex-col gap-0.5">
            <span className="text-lg font-semibold">{b.who ? (proposedMe ? "You should fix this" : `${b.who} should fix this`) : "Nobody free to fix this"}</span>
            <span className="text-[13px] text-muted">About {b.eta_agent} hours.{free != null ? (free < 0 ? ` That puts them ${-free}h over today's cap.` : ` Leaves them ${free}h today.`) : ""}</span>
          </div>
        </div>
        {p?.why && <p className="text-[13px] leading-relaxed text-text-2">{p.why}</p>}
        {(proposedMe || lead) && b.who && (
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={primary} disabled={busy} onClick={() => act("accept", { eta: eta ? Number(eta) : null })}>
              {proposedMe ? "I'll take it" : `Assign to ${b.who.split(" ")[0]}`}
            </button>
            {lead && <button type="button" className={secondary} onClick={() => setMoving(!moving)}>Someone else</button>}
            {proposedMe && !lead && <button type="button" className={secondary} disabled={busy} onClick={() => act("decline", { reason: "busy" })}>Pass</button>}
            <label className="ml-auto flex items-center gap-1.5 text-xs text-muted">ETA
              <input type="number" min={0.5} step={0.5} className={`${inputCls} h-9 w-16`} placeholder={String(b.eta_agent)} value={eta} onChange={(e) => setEta(e.target.value)} />h
            </label>
          </div>
        )}
        {moving && (
          <div className="enter flex flex-wrap items-center gap-2">
            <select aria-label="Move to" className={`${inputCls} h-9 min-w-0 flex-1`} value={to} onChange={(e) => setTo(e.target.value)}>
              <option value="">Pick a person</option>
              {r.people.filter((x) => x.id !== b.who_id).sort((a, z) => z.left - a.left).map((x) =>
                <option key={x.id} value={x.id} disabled={x.shift !== "on"}>{x.name} ({x.shift !== "on" ? "off" : `${x.left}h free`})</option>)}
            </select>
            <button type="button" className={secondary} disabled={!to || busy} onClick={() => act("reassign", { to })}>Move</button>
          </div>
        )}
        {!ask && <p className="text-xs text-muted">Waiting for {b.who || "a lead"} to accept.</p>}
        {err && <p className="text-xs text-bad">{err}</p>}
      </div>
    );
  }
  if (b.stage === "fixing") {
    return (
      <div className={card}>
        <div className="flex items-center gap-3">
          <Initials name={b.who} size={40} />
          <div className="flex flex-col gap-0.5">
            <span className="text-lg font-semibold">{proposedMe ? "You're fixing this" : `${b.who} is fixing this`}</span>
            <span className="text-[13px] text-muted">{b.eta_owner}h agreed{b.eta_owner !== b.eta_agent ? ` (the agent guessed ${b.eta_agent}h)` : " with the agent"}.</span>
          </div>
        </div>
        {(proposedMe || lead) && <button type="button" className={ask ? primary : secondary} disabled={busy} onClick={() => act("fixed")}>Mark fixed on staging</button>}
      </div>
    );
  }
  if (b.stage === "fixed") {
    return (
      <div className={card}>
        <span className="text-lg font-semibold">Fixed by {b.who}. Check it on staging.</span>
        <span className="text-[13px] text-muted">{r.repro.run ? "Run the repro again: it should come back clean." : "Follow the repro steps and confirm."}</span>
        {ask ? <button type="button" className={primary} disabled={busy} onClick={() => act("verify")}>Verified on staging</button> : <span className="text-xs text-muted">Waiting for a tester.</span>}
      </div>
    );
  }
  return (
    <div className={card}>
      <span className="flex items-center gap-2 text-lg font-semibold text-ok"><Check size={18} />Verified</span>
      <span className="text-[13px] text-muted">Fixed by {b.who}. This no longer blocks the release.</span>
    </div>
  );
}

function Repro({ r, bid, reload }: { r: Room; bid: string; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const res = r.repro.result;
  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Reproduce it</h2>
        {r.repro.run && <Button size="sm" disabled={busy} onClick={async () => {
          setBusy(true); setErr(null);
          try { await post(`/bugs/${bid}/repro/run`); reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
          setBusy(false);
        }}><Play size={12} /> {busy ? "Running" : "Run repro"}</Button>}
      </div>
      {r.repro.steps.length === 0 && <p className="text-sm text-muted">The repro agent is working on it.</p>}
      <ol className="flex flex-col gap-3">
        {r.repro.steps.map((s, i) => (
          <li key={i} className="grid grid-cols-[24px_minmax(0,1fr)] gap-3 text-sm">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-raised font-mono text-[11px] text-text-2">{i + 1}</span>
            <span>{s.do}<span className="block break-words font-mono text-[11px] text-muted">{s.detail}</span></span>
          </li>
        ))}
      </ol>
      {r.repro.script && <pre className="overflow-x-auto rounded-lg border border-line bg-panel p-3 font-mono text-[11.5px] text-text-2">{r.repro.script}</pre>}
      {err && <p className="text-xs text-bad">{err}</p>}
      {res && (
        <div className="flex flex-col gap-2 rounded-lg border border-line p-3">
          <p className={`text-sm ${res.ok ? "text-bad" : "text-ok"}`}>{res.summary} <span className="font-mono text-xs text-muted">{res.ms}ms</span></p>
          {res.table && (
            <table className="w-full table-fixed text-left font-mono text-[11px]">
              <thead><tr>{res.table.columns.map((c) => <th key={c} className="pb-1 font-normal text-muted">{c}</th>)}</tr></thead>
              <tbody>{res.table.rows.map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j} className="truncate py-0.5 text-text-2">{String(v)}</td>)}</tr>)}</tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}

function Cause({ r }: { r: Room }) {
  const t = r.evidence.theory;
  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-sm font-semibold">Cause</h2>
      {!t && <p className="text-sm text-muted">The root-cause agent is working on it.</p>}
      {t && (
        <>
          <div className="flex gap-4">
            <span className="w-14 shrink-0 font-mono text-2xl text-text">{t.confidence}%</span>
            <p className="whitespace-pre-line text-sm leading-relaxed">{t.text}</p>
          </div>
          <details className="text-sm">
            <summary className="cursor-pointer text-muted">How the agent got there</summary>
            <ol className="mt-3 flex flex-col gap-2">
              {r.evidence.steps.map((s) => <li key={s.name} className="text-xs"><span className="text-text-2">{s.name}:</span> <span className="whitespace-pre-line text-muted">{s.summary}</span></li>)}
            </ol>
            <p className="mt-2 text-[11px] text-faint">{r.evidence.source === "model" ? "Theory written by the model, every claim cited." : "Theory built from evidence only (model offline)."}</p>
          </details>
        </>
      )}
      {r.suggested[0] && <p className="text-xs text-muted">Priority: agent suggested {r.suggested[0]} ({r.suggested[1]}). Current: {r.bug.priority}.</p>}
    </section>
  );
}
