"use client";

import Link from "next/link";
import { Watch } from "@/components/watch";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ArrowRight, CheckCircle, WarningCircle, XCircle } from "@phosphor-icons/react";
import { post, usePoll } from "@/lib/api";
import { Field, Section, Shell, STAGE_LABEL, inputCls, STAGE_TONE } from "@/components/shell";
import { Button } from "@/components/ui";
import { Board } from "@/components/board";
import { TestSession } from "@/components/session";

type Bug = {
  id: string; title: string; body: string; env: string; severity: string; priority: string; suggested: string; suggested_why: string;
  blocker: number; status: string; reporter: string; reporter_name: string; reporter_kind: string; assignee: string | null;
  assignee_name: string | null; links: string[]; evidence: Evidence | string;
};
type Evidence = { steps: { name: string; summary: string; cites: string[] }[]; theory: { text: string; confidence: number; cites: string[] };
  source: string; assignee: { user: string | null; name: string; why: string } | null; regression: boolean };
type Test = { id: string; charter: string; kind: string; target: string; status: string; by: string; result: string; risk: string };
type Risk = { id: string; target: string; kind: string; level: string; note: string; status: string; tested_by: string | null };
type R = {
  release: { id: string; project: string; name: string; stage: string; window_start: string; window_end: string; scope_type: string;
    scope_value: string; migration: number; rollback_plan: string; postmortem: string | null };
  role: string; stages: string[]; project: { id: string; name: string; demo: number; staging_url: string | null };
  prs: { id: string; label: string; url?: string; state?: string; merged_at?: string }[]; files: number;
  risks: Risk[]; tests: Test[]; bugs: Bug[]; gates: { id: string; label: string; ok: boolean; detail: string }[];
  decisions: { id: string; text: string; by: string }[]; events: { seq: number; actor: string; kind: string; ts: number; data: Record<string, unknown> }[];
  assignable: { id: string; name: string; shift: string; hours_today: number }[];
};

export default function ReleasePage() {
  const { pid, rid } = useParams<{ pid: string; rid: string }>();
  const { data: d, error, reload } = usePoll<R>(`/releases/${rid}`, 2500);
  const [view, setView] = useState<string | null>(null);
  if (!d) return <Shell crumbs={[{ label: "Release" }]}><p className="text-sm text-muted">{error ?? "Loading"}</p></Shell>;

  const r = d.release;
  const current = view ?? r.stage;
  const idx = d.stages.indexOf(r.stage);
  const lead = d.role === "owner" || d.role === "lead";
  const canTest = ["owner", "lead", "engineer", "tester"].includes(d.role);
  const guest = d.role === "client_guest";

  return (
    <Shell wide crumbs={[{ label: d.project.name, href: `/p/${pid}` }, { label: r.name }]}>
      <div className="flex flex-col gap-8">
        <header className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">{r.name}</h1>
          <p className="text-sm text-muted">
            {r.window_start ? `Deploy window ${r.window_start.replace("T", " ")}${r.window_end ? ` to ${r.window_end.replace("T", " ")}` : ""}` : "No deploy window yet"}
            {r.migration ? ". Includes a data migration" : ""}. Scope: {r.scope_type} {r.scope_value}.
          </p>
        </header>

        <nav aria-label="Stages" className="grid grid-cols-3 gap-1 sm:grid-cols-6">
          {d.stages.map((s, i) => (
            <button key={s} type="button" onClick={() => setView(s)}
              className={`flex flex-col gap-2 rounded-lg px-3 pb-3 pt-2 text-left transition-colors ${current === s ? "bg-raised" : "hover:bg-panel"}`}>
              <span className={`h-1 rounded-full transition-colors ${i < idx ? "bg-ok/50" : i === idx ? STAGE_TONE[s] ?? "bg-text" : "bg-line-strong"}`} />
              <span className={`text-sm ${i === idx ? "text-text" : "text-muted"}`}>{STAGE_LABEL[s]}</span>
            </button>
          ))}
        </nav>

        {current === "plan" && <Plan d={d} />}
        {current === "build" && <Build d={d} guest={guest} canTest={canTest} reload={reload} />}
        {current === "staging" && <Work d={d} pid={pid} env="staging" canTest={canTest} guest={guest} reload={reload} />}
        {!guest && <Affects rid={rid} pid={pid} />}
        {current === "go_no_go" && <GoNoGo d={d} lead={lead} reload={reload} />}
        {(current === "go_no_go" || current === "live" || current === "closed") && !guest && <Watch rid={rid} pid={pid} lead={lead} stage={current} />}
        {current === "live" && <Live d={d} pid={pid} canTest={canTest} guest={guest} />}
        {current === "closed" && <Closed d={d} />}

        {lead && current === r.stage && r.stage !== "closed" && r.stage !== "go_no_go" && (
          <Advance rid={rid} label={`Move to ${STAGE_LABEL[d.stages[idx + 1]]}`} reload={reload} />
        )}

        {!guest && d.events.length > 0 && (
          <details className="border-t border-line pt-6">
            <summary className="cursor-pointer text-sm text-muted">Activity ({d.events.length})</summary>
            <ol className="mt-4 flex flex-col gap-2 text-xs text-muted">
              {d.events.map((e) => (
                <li key={e.seq}><span className="font-mono text-faint">{new Date(e.ts * 1000).toLocaleString()}</span> {e.actor} {e.kind.replace(/_/g, " ")}</li>
              ))}
            </ol>
          </details>
        )}
      </div>
    </Shell>
  );
}

function Advance({ rid, label, reload, override = false }: { rid: string; label: string; reload: () => void; override?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const go = async () => {
    setBusy(true); setErr(null);
    try { await post(`/releases/${rid}/advance`, { override_reason: reason }); reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };
  return (
    <div className="flex flex-wrap items-center gap-3 border-t border-line pt-6">
      {override && <input className={`${inputCls} min-w-[280px] flex-1`} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for going live with failing gates (logged)" />}
      <Button variant="primary" disabled={busy} onClick={go}>{label} <ArrowRight size={14} /></Button>
      {err && <span className="text-sm text-bad">{err}</span>}
    </div>
  );
}

function Plan({ d }: { d: R }) {
  return (
    <div className="grid gap-8 md:grid-cols-2">
      <Section title="What ships">
        <p className="text-sm text-text-2">{d.prs.length} pull requests match {d.release.scope_type} &ldquo;{d.release.scope_value}&rdquo;, touching {d.files} files. New PRs join as they&apos;re opened.</p>
        <PRList prs={d.prs.slice(0, 8)} />
      </Section>
      <Section title="Who's around for the window">
        <ul className="flex flex-col gap-2 text-sm">
          {d.assignable.map((p) => (
            <li key={p.id} className="flex justify-between"><span>{p.name}</span>
              <span className={`text-xs ${p.shift !== "on" || p.hours_today >= 12 ? "text-bad" : "text-muted"}`}>{p.shift !== "on" ? "off shift" : `${p.hours_today}h today`}</span></li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

function PRList({ prs }: { prs: R["prs"] }) {
  if (!prs.length) return <p className="text-sm text-muted">No PRs in scope yet.</p>;
  return (
    <ul className="flex flex-col divide-y divide-line rounded-xl border border-line text-sm">
      {prs.map((p) => (
        <li key={p.id} className="px-4 py-2.5">
          {p.url ? <a href={p.url} target="_blank" rel="noreferrer" className="text-text hover:text-accent">{p.label}</a> : p.label}
        </li>
      ))}
    </ul>
  );
}

function Build({ d, guest, canTest, reload }: { d: R; guest: boolean; canTest: boolean; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  if (guest) return <p className="text-sm text-muted">The team is building this release.</p>;
  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <Section title={`Pull requests (${d.prs.length})`}><PRList prs={d.prs} /></Section>
      <div className="flex flex-col gap-10">
        <Section title="Pre-mortem" action={canTest && <Button size="sm" disabled={busy} onClick={async () => { setBusy(true); await post(`/releases/${d.release.id}/premortem`); setBusy(false); reload(); }}>{busy ? "Checking" : "Re-check the diff"}</Button>}>
          <Risks risks={d.risks} />
        </Section>
        <Decisions d={d} canWrite={canTest} reload={reload} />
      </div>
    </div>
  );
}

function Risks({ risks }: { risks: Risk[] }) {
  if (!risks.length) return <p className="text-sm text-muted">No risks found in the diff yet. They&apos;re recomputed as PRs land.</p>;
  return (
    <ul className="flex flex-col gap-3">
      {risks.map((r) => (
        <li key={r.id} className="flex gap-3 text-sm">
          {/* Severity, not a call to act: red for high, blue for the rest. */}
          <span className={`mt-0.5 w-12 shrink-0 text-xs ${r.level === "high" ? "text-bad" : "text-info"}`}>{r.level}</span>
          <span className="min-w-0 flex-1">
            <span className="font-mono text-xs text-text">{r.target.split(":").slice(1).join(":").split("@")[0].split("#").pop()}</span>
            <span className="ml-2 text-text-2">{r.kind}</span>
            {r.note && r.note !== r.kind && <span className="block text-xs text-muted">{r.note}</span>}
          </span>
          <span className={`shrink-0 text-xs ${r.status === "verified" ? "text-ok" : r.status === "failed" ? "text-bad" : "text-muted"}`}>{r.status}</span>
        </li>
      ))}
    </ul>
  );
}

function Decisions({ d, canWrite, reload }: { d: R; canWrite: boolean; reload: () => void }) {
  const [text, setText] = useState("");
  return (
    <Section title="Decisions">
      {d.decisions.length === 0 && <p className="text-sm text-muted">Decisions pinned here are followed by every agent.</p>}
      <ul className="flex flex-col gap-2 text-sm">{d.decisions.map((x) => <li key={x.id}>{x.text} <span className="text-xs text-muted">{x.by}</span></li>)}</ul>
      {canWrite && (
        <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); if (!text.trim()) return; await post(`/releases/${d.release.id}/decisions`, { text }); setText(""); reload(); }}>
          <label htmlFor="dec" className="sr-only">New decision</label>
          <input id="dec" className={`${inputCls} flex-1`} value={text} onChange={(e) => setText(e.target.value)} placeholder="Skip soft-deleted users in this release" />
          <Button type="submit">Pin</Button>
        </form>
      )}
    </Section>
  );
}

function Work({ d, pid, env, canTest, guest, reload }: { d: R; pid: string; env: string; canTest: boolean; guest: boolean; reload: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [tab, setTab] = useState<"bugs" | "testing">("bugs");
  const bugs = d.bugs.filter((b) => b.env === env);
  if (guest) {
    return (
      <Section title={env === "staging" ? "Testing" : "Issues after go-live"}>
        <p className="text-sm text-text-2">{bugs.filter((b) => b.status !== "verified").length} open, {bugs.filter((b) => b.status === "verified").length} fixed and verified.</p>
      </Section>
    );
  }
  const high = d.risks.filter((r) => r.level === "high");
  const runTester = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await post(`/releases/${d.release.id}/tester/run`);
      setMsg(`Agent tester ran ${r.ran} checks (${r.failed} failed and were filed as bugs) and asked your testers to run ${r.manual} it can't do itself.`);
      reload();
    } finally { setBusy(false); }
  };
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
          <span className="flex items-center gap-1.5"><span className="h-1.5 w-3 rounded-full bg-accent" />Agents test, reproduce, find the cause, propose an owner and an ETA</span>
          <span className="flex items-center gap-1.5"><span className="h-1.5 w-3 rounded-full bg-ok" />People accept, agree the ETA, fix and verify</span>
        </p>
        <div className="flex gap-2">
          {canTest && <Button size="sm" variant="quiet" disabled={busy} onClick={async () => { const r = await post(`/releases/${d.release.id}/import_issues`); setMsg(`Imported ${r.imported} GitHub issue${r.imported === 1 ? "" : "s"}. Agents are on them.`); reload(); }}>Import GitHub issues</Button>}
          {canTest && env === "staging" && <Button size="sm" disabled={busy} onClick={runTester}>{busy ? "Agent tester running" : "Run agent tester"}</Button>}
          {canTest && <Button size="sm" variant="primary" onClick={() => setOpen(!open)}>{open ? "Cancel" : "Report a bug"}</Button>}
        </div>
      </div>
      {msg && <p className="text-xs text-text-2">{msg}</p>}
      {open && <ReportForm rid={d.release.id} env={env} onDone={() => { setOpen(false); reload(); }} />}
      {env === "staging" && (
        <div role="tablist" className="flex gap-1 border-b border-line">
          {(["bugs", "testing"] as const).map((k) => (
            <button key={k} role="tab" type="button" aria-selected={tab === k} onClick={() => setTab(k)}
              className={`-mb-px border-b-2 px-4 py-2 text-sm ${tab === k ? "border-accent text-text" : "border-transparent text-muted hover:text-text"}`}>
              {k === "bugs" ? "Bugs" : `Testing: ${high.filter((r) => r.status === "verified").length} of ${high.length} high risks verified`}
            </button>
          ))}
        </div>
      )}
      {(env !== "staging" || tab === "bugs") && <Board rid={d.release.id} pid={pid} env={env} />}
      {env === "staging" && tab === "testing" && (
        <div className="flex flex-col gap-10">
          <TestSession rid={d.release.id} pid={pid} />
          <details className="rounded-xl border border-line p-5">
            <summary className="cursor-pointer text-sm">Risk coverage from the pre-mortem</summary>
            <div className="mt-6"><Tests d={d} canTest={canTest} reload={reload} /></div>
          </details>
        </div>
      )}
    </div>
  );
}

function ReportForm({ rid, env, onDone }: { rid: string; env: string; onDone: () => void }) {
  const [f, setF] = useState({ title: "", body: "", severity: "major", blocker: false });
  const [err, setErr] = useState<string | null>(null);
  const report = async () => {
    if (!f.title.trim()) return setErr("Add a title.");
    try { await post(`/releases/${rid}/bugs`, { ...f, env }); onDone(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <div className="grid gap-3 rounded-xl border border-line bg-panel p-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <Field label="What broke"><input className={inputCls} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="Checkout fails for saved cards" /></Field>
      <Field label="Steps and what you saw"><input className={inputCls} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} placeholder="Test account, card ending 4242, Pay" /></Field>
      <div className="flex flex-wrap items-center gap-4 md:col-span-2">
        <select aria-label="Severity" className={`${inputCls} w-36`} value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}>
          {["critical", "major", "minor", "trivial"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <label className="flex items-center gap-2 text-sm text-text-2"><input type="checkbox" checked={f.blocker} onChange={(e) => setF({ ...f, blocker: e.target.checked })} /> Release blocker</label>
        <span className="flex-1 text-xs text-muted">Agents take it from here: reproduce, find the cause, propose an owner and an ETA.</span>
        {err && <span className="text-xs text-bad">{err}</span>}
        <Button variant="primary" onClick={report}>Report</Button>
      </div>
    </div>
  );
}

function Tests({ d, canTest, reload }: { d: R; canTest: boolean; reload: () => void }) {
  const msg: string | null = null;
  const high = d.risks.filter((r) => r.level === "high");
  const verified = high.filter((r) => r.status === "verified").length;
  return (
    <div className="flex flex-col gap-10">
      <Section title="Coverage of high risks">
        <div className="flex items-baseline gap-2"><span className="font-mono text-2xl">{verified}/{high.length}</span><span className="text-sm text-muted">verified on staging</span></div>
        <Risks risks={d.risks} />
      </Section>
      <Section title={`Tests (${d.tests.length})`}>
        {msg && <p className="text-xs text-text-2">{msg}</p>}
        {!d.project.staging_url && !d.project.demo && <p className="text-xs text-muted">Set a staging URL on the project to let the agent tester call endpoints.</p>}
        <ul className="flex flex-col gap-2">
          {d.tests.map((t) => (
            <li key={t.id} className="flex items-start gap-3 text-sm">
              {t.status === "passed" ? <CheckCircle size={16} className="mt-0.5 shrink-0 text-ok" /> : t.status === "failed" ? <XCircle size={16} className="mt-0.5 shrink-0 text-bad" /> :
               t.status === "manual" ? <WarningCircle size={16} className="mt-0.5 shrink-0 text-accent" /> : <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-line-strong" />}
              <span className="min-w-0 flex-1">{t.charter}{t.result && <span className="block break-words text-xs text-muted">{t.result}</span>}</span>
              {canTest && t.status === "manual" && (
                <span className="flex shrink-0 gap-1">
                  <Button size="sm" variant="quiet" onClick={async () => { await post(`/tests/${t.id}`, { status: "passed" }); reload(); }}>Pass</Button>
                  <Button size="sm" variant="quiet" onClick={async () => { await post(`/tests/${t.id}`, { status: "failed" }); reload(); }}>Fail</Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

function GoNoGo({ d, lead, reload }: { d: R; lead: boolean; reload: () => void }) {
  const [plan, setPlan] = useState(d.release.rollback_plan);
  const failing = d.gates.filter((g) => !g.ok);
  const isCurrent = d.release.stage === "go_no_go";
  return (
    <div className="grid gap-10 lg:grid-cols-2">
      <Section title="Gates">
        <ul className="flex flex-col gap-3">
          {d.gates.map((g) => (
            <li key={g.id} className="flex items-start gap-3 text-sm">
              {g.ok ? <CheckCircle size={18} className="shrink-0 text-ok" /> : <XCircle size={18} className="shrink-0 text-bad" />}
              <span className="flex-1">{g.label}<span className="block text-xs text-muted">{g.detail}</span></span>
              {g.id === "client" && !g.ok && <Button size="sm" onClick={async () => { try { await post(`/releases/${d.release.id}/client_update/approve`); } catch (e) { alert(e instanceof Error ? e.message : e); } reload(); }}>Approve update</Button>}
            </li>
          ))}
        </ul>
        {isCurrent && lead && (failing.length === 0
          ? <Advance rid={d.release.id} label="Go live" reload={reload} />
          : <Advance rid={d.release.id} label="Go live anyway" override reload={reload} />)}
      </Section>
      <Section title="Rollback plan">
        <textarea aria-label="Rollback plan" className={`${inputCls} h-40 py-2`} value={plan} disabled={!lead} onChange={(e) => setPlan(e.target.value)}
          placeholder="How we undo this release, step by step" />
        {lead && <Button className="self-start" onClick={async () => { await post(`/releases/${d.release.id}/rollback`, { text: plan }); reload(); }}>Save plan</Button>}
      </Section>
    </div>
  );
}

/** Which teams this release reaches, from the areas its changes fall in. */
function Affects({ rid, pid }: { rid: string; pid: string }) {
  const { data } = usePoll<{ tracks: { track: string; name: string; kind: string; areas: string[] }[] }>(`/releases/${rid}/tracks`, 60000);
  if (!data || !data.tracks.length) return null;
  return (
    <p className="text-[13px] text-muted">
      Affects{" "}
      {data.tracks.map((t, i) => (
        <span key={t.track}>{i > 0 && (i === data.tracks.length - 1 ? " and " : ", ")}
          <Link href={`/p/${pid}/tracks/${t.track}`} className="text-text-2 hover:underline">{t.name}</Link> ({t.areas.map((a) => a[0].toUpperCase() + a.slice(1)).join(", ")})
        </span>
      ))}.
    </p>
  );
}

function Live({ d, pid, canTest, guest }: { d: R; pid: string; canTest: boolean; guest: boolean }) {
  return <Work d={d} pid={pid} env="production" canTest={canTest} guest={guest} reload={() => {}} />;
}

function Closed({ d }: { d: R }) {
  if (!d.release.postmortem) return <p className="text-sm text-muted">The postmortem is written from the event log when the release closes.</p>;
  return (
    <article className="flex max-w-[70ch] flex-col gap-3 text-sm leading-relaxed text-text-2">
      {d.release.postmortem.split("\n").map((l, i) => l.startsWith("# ") ? <h2 key={i} className="text-lg font-semibold text-text">{l.slice(2)}</h2>
        : l.startsWith("## ") ? <h3 key={i} className="mt-3 text-sm font-semibold text-text">{l.slice(3)}</h3>
        : l.startsWith("- ") ? <p key={i} className="pl-4">{l.slice(2)}</p> : l ? <p key={i}>{l}</p> : null)}
    </article>
  );
}
