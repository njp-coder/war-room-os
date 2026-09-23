"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Check, Eye, Plus } from "@phosphor-icons/react";
import { post, usePoll } from "@/lib/api";
import { inputCls } from "./shell";
import { Button } from "./ui";

type Target = { kind: "endpoint" | "table" | "file"; id: string; label: string; why: string; on: boolean };
type Finding = { at: number; rule: string; title: string; incident: string | null; action: string };
type W = { release: string; status: "not_set" | "scheduled" | "watching" | "done"; hours: number; targets: Target[];
  started_at: number | null; ends_at: number | null; findings: Finding[]; summary: string | null; monitoring: boolean; suggested?: boolean;
  compare?: { query: string; before_ms: number; now_ms?: number | null; after_ms?: number }[];
  baseline?: { compare?: { query: string; before_ms: number; after_ms: number }[] } };

const DURATIONS = [2, 6, 12, 24, 48];
const KIND_WORD = { endpoint: "Requests", table: "Tables", file: "Changed code (errors from here count)" };

/** What to watch after go-live, for how long, and what happened. Configured per release. */
export function Watch({ rid, pid, lead, stage }: { rid: string; pid: string; lead: boolean; stage: string }) {
  const { data: w, reload } = usePoll<W>(`/releases/${rid}/watch`, 10000);
  const [editing, setEditing] = useState(false);
  if (!w) return null;
  const watching = w.status === "watching";
  const done = w.status === "done";
  const configuring = editing || (!watching && !done && lead);

  return (
    <section className="flex flex-col gap-4 rounded-2xl border border-line px-5 py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="flex items-center gap-2 text-[15px] font-semibold"><Eye size={16} className="text-text-2" />Watch after release</h2>
        {watching && lead && !editing && (
          <span className="flex gap-2">
            <Button size="sm" onClick={() => setEditing(true)}>Change</Button>
            <Button size="sm" variant="quiet" onClick={async () => { await post(`/releases/${rid}/watch/stop`); reload(); }}>Stop watching</Button>
          </span>
        )}
      </div>
      {!w.monitoring && (
        <p className="text-[13px] text-muted">Nothing to watch with yet: <Link href={`/p/${pid}?view=setup`} className="text-text-2 underline-offset-2 hover:underline">connect production monitoring or a log source in Setup</Link>.</p>
      )}
      {watching && !editing && <Watching w={w} pid={pid} />}
      {done && <Done w={w} pid={pid} />}
      {configuring && !done && <Configure w={w} rid={rid} stage={stage} onSaved={() => { setEditing(false); reload(); }} cancel={editing ? () => setEditing(false) : undefined} />}
      {!configuring && !watching && !done && (
        <p className="text-[13px] text-muted">Starts when the release goes live: {w.hours} h on {w.targets.filter((t) => t.on).length} things this release changed.</p>
      )}
    </section>
  );
}

function Configure({ w, rid, stage, onSaved, cancel }: { w: W; rid: string; stage: string; onSaved: () => void; cancel?: () => void }) {
  const [hours, setHours] = useState(w.hours);
  const [custom, setCustom] = useState(DURATIONS.includes(w.hours) ? "" : String(w.hours));
  const [targets, setTargets] = useState<Target[]>(w.targets);
  const [adding, setAdding] = useState<{ kind: Target["kind"]; label: string }>({ kind: "table", label: "" });
  const [busy, setBusy] = useState(false);
  useEffect(() => { setTargets(w.targets); }, [w.targets.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const on = targets.filter((t) => t.on).length;
  const toggle = (i: number) => setTargets(targets.map((t, j) => (j === i ? { ...t, on: !t.on } : t)));
  const save = async () => {
    setBusy(true);
    try { await post(`/releases/${rid}/watch`, { hours: Number(custom) || hours, targets }); onSaved(); } finally { setBusy(false); }
  };
  const groups = (["endpoint", "table", "file"] as const).map((k) => ({ k, items: targets.map((t, i) => ({ t, i })).filter((x) => x.t.kind === k) })).filter((g) => g.items.length);
  return (
    <div className="flex flex-col gap-5">
      <p className="text-[13px] text-muted">
        {w.suggested ? "Suggested from what this release changes. " : ""}During the window, a watched query that gets {`2×`} slower than before the release, or any new error in the changed code,
        raises a war room right away instead of waiting for the usual checks.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 text-[13px] text-text-2">For</span>
        {DURATIONS.map((h) => (
          <button key={h} type="button" onClick={() => { setHours(h); setCustom(""); }} aria-pressed={hours === h && !custom}
            className={`h-8 rounded-lg border px-3 text-[13px] ${hours === h && !custom ? "border-text-2 bg-raised text-text" : "border-line-strong text-muted hover:text-text"}`}>
            {h < 24 ? `${h} hours` : h === 24 ? "1 day" : "2 days"}
          </button>
        ))}
        <label className="flex items-center gap-1.5 text-[13px] text-muted">or
          <input type="number" min={0.5} max={168} step={0.5} value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="hours" className={`${inputCls} h-8 w-20`} />
        </label>
      </div>
      <div className="flex flex-col gap-4">
        {groups.map((g) => (
          <div key={g.k} className="flex flex-col gap-1.5">
            <h3 className="text-xs font-semibold text-text-2">{KIND_WORD[g.k]}</h3>
            <div className="flex flex-wrap gap-1.5">
              {g.items.map(({ t, i }) => (
                <button key={t.kind + t.id} type="button" onClick={() => toggle(i)} aria-pressed={t.on} title={t.why}
                  className={`flex items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-[11px] ${t.on ? "border-text-2 bg-raised text-text" : "border-line text-faint line-through"}`}>
                  {t.on && <Check size={10} />}{t.label}
                </button>
              ))}
            </div>
          </div>
        ))}
        {!groups.length && <p className="text-[13px] text-muted">No changed code linked to this release yet. Add what to watch below.</p>}
        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => {
          e.preventDefault();
          if (!adding.label.trim()) return;
          setTargets([...targets, { kind: adding.kind, id: adding.label.trim(), label: adding.label.trim(), why: "added by hand", on: true }]);
          setAdding({ ...adding, label: "" });
        }}>
          <label className="sr-only" htmlFor="watch-kind">Kind</label>
          <select id="watch-kind" className={`${inputCls} h-8 w-32 text-xs`} value={adding.kind} onChange={(e) => setAdding({ ...adding, kind: e.target.value as Target["kind"] })}>
            <option value="table">Table</option><option value="file">Code file</option><option value="endpoint">Request</option>
          </select>
          <label className="sr-only" htmlFor="watch-add">Name</label>
          <input id="watch-add" className={`${inputCls} h-8 w-56 text-xs`} value={adding.label} onChange={(e) => setAdding({ ...adding, label: e.target.value })}
            placeholder={adding.kind === "table" ? "payments" : adding.kind === "file" ? "backend/app/services/pricing.py" : "POST /orders"} />
          <Button size="sm" type="submit"><Plus size={12} />Add</Button>
        </form>
      </div>
      <div className="flex items-center gap-3">
        <Button variant="primary" disabled={busy || !on} onClick={save}>
          {w.status === "watching" ? "Update the watch" : stage === "live" ? `Start watching ${on} things` : `Watch ${on} things after go-live`}
        </Button>
        {cancel && <Button variant="quiet" onClick={cancel}>Cancel</Button>}
      </div>
    </div>
  );
}

function Watching({ w, pid }: { w: W; pid: string }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 30000); return () => clearInterval(t); }, []);
  const total = (w.ends_at ?? 0) - (w.started_at ?? 0);
  const gone = Math.min(Math.max(Date.now() / 1000 - (w.started_at ?? 0), 0), total);
  const left = Math.max(total - gone, 0);
  const on = w.targets.filter((t) => t.on);
  const rows = (w.compare ?? []).filter((c) => c.now_ms != null);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <div className="flex justify-between text-[13px]">
          <span>Watching {on.length} things: {on.slice(0, 4).map((t) => t.label).join(", ")}{on.length > 4 ? ` and ${on.length - 4} more` : ""}</span>
          <span className="font-mono text-xs text-muted">{fmt(left)} left</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-[#1c1d21]"><div className="h-full bg-text-2" style={{ width: `${(gone / Math.max(total, 1)) * 100}%` }} /></div>
      </div>
      {w.findings.length ? <Findings w={w} pid={pid} /> : <p className="text-[13px] text-muted">Nothing unusual since go-live.</p>}
      {rows.length > 0 && <Compare rows={rows.map((r) => ({ query: r.query, before: r.before_ms, after: r.now_ms! }))} afterLabel="Now" />}
    </div>
  );
}

function Done({ w, pid }: { w: W; pid: string }) {
  const clean = !w.findings.length && !(w.baseline?.compare ?? []).some((c) => c.after_ms >= 2 * Math.max(c.before_ms, 1));
  return (
    <div className="flex flex-col gap-4">
      <p className={`flex items-start gap-2 text-sm ${clean ? "text-ok" : ""}`}>{clean && <Check size={16} className="mt-0.5 shrink-0" />}{w.summary}</p>
      {w.findings.length > 0 && <Findings w={w} pid={pid} />}
      {(w.baseline?.compare ?? []).length > 0 && <Compare rows={w.baseline!.compare!.map((c) => ({ query: c.query, before: c.before_ms, after: c.after_ms }))} afterLabel="After" />}
    </div>
  );
}

function Findings({ w, pid }: { w: W; pid: string }) {
  return (
    <ul className="flex flex-col gap-1.5">
      {w.findings.map((f, i) => (
        <li key={i} className="flex flex-wrap items-baseline gap-2 text-[13px]">
          <span className="font-mono text-[11px] text-muted">{new Date(f.at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
          <span className="min-w-0 flex-1">{f.title}</span>
          {f.incident ? <Link href={`/p/${pid}/i/${f.incident}`} className="text-xs text-text-2 underline-offset-2 hover:underline">{f.action === "open" ? "War room" : "Proposed war room"}</Link>
            : <span className="text-xs text-muted">held back as likely noise</span>}
        </li>
      ))}
    </ul>
  );
}

function Compare({ rows, afterLabel }: { rows: { query: string; before: number; after: number }[]; afterLabel: string }) {
  return (
    <table className="w-full table-fixed text-left text-xs">
      <thead><tr className="text-muted"><th className="w-[64%] pb-1 font-normal">Watched query</th><th className="pb-1 text-right font-normal">Before</th><th className="pb-1 text-right font-normal">{afterLabel}</th></tr></thead>
      <tbody>
        {rows.slice(0, 8).map((r) => {
          const worse = r.after >= 2 * Math.max(r.before, 1);
          return (
            <tr key={r.query} className="border-t border-line">
              <td className="truncate py-1.5 pr-3 font-mono text-[11px] text-text-2" title={r.query}>{r.query}</td>
              <td className="py-1.5 text-right font-mono text-muted">{r.before} ms</td>
              <td className={`py-1.5 text-right font-mono ${worse ? "text-bad" : "text-text-2"}`}>{r.after} ms</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function fmt(s: number) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${m} m` : `${m} m`;
}
