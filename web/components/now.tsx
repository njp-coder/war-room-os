"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Check, Robot, User } from "@phosphor-icons/react";
import { get, post, usePoll } from "@/lib/api";
import { Field, STAGE_TEXT, STAGE_TONE, inputCls } from "./shell";
import { Button } from "./ui";

export type QueueBug = { id: string; title: string; hours: number; proposed: string | null; proposed_id: string | null };
export type NowData = {
  sentence: string; role: string; synced: number | null; counts: Record<string, number>; setup_todo: number;
  needs: { kind: string; id: string; context: string; title: string; detail?: string; action: string }[];
  release: { id: string; name: string; stage: string; window: string; waiting_owner: number; fixing: number; agents_on: number; verify: number;
    risks_tested: number; risks_high: number; queue: QueueBug[] } | null;
  team: TeamRow[];
};
export type TeamRow = { id: string; name: string; shift: string; worked: number; agreed: number; proposed: number; cap: number };

const STAGES = ["plan", "build", "staging", "go_no_go", "live"];
const STAGE_WORD: Record<string, string> = { plan: "Plan", build: "Build", staging: "Staging", go_no_go: "Go or no-go", live: "Live", closed: "Closed" };

/** Things only a person can do. The only amber on the page. */
export function NeedsYou({ pid, now, reload }: { pid: string; now: NowData; reload: () => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  if (!now.needs.length) return null;
  const act = async (id: string, action: string, go?: string) => {
    setBusy(id);
    try { await post(`/incidents/${id}/act`, { action, detail: action === "dismiss" ? "not an incident" : "" }); } finally { setBusy(null); }
    if (go) router.push(go); else reload();
  };
  return (
    <section className="flex flex-col gap-2.5">
      <h2 className="text-[13px] font-semibold text-accent">Needs you</h2>
      <div className="grid gap-3.5 md:grid-cols-2">
        {now.needs.map((n) => (
          <div key={n.kind + n.id} className="arrive flex flex-col gap-3.5 rounded-2xl border border-[#4a3a1e] bg-[#1a1712] px-5 py-5">
            <div className="flex flex-col gap-1">
              <span className="text-xs text-muted">{n.context}</span>
              <span className="text-lg font-semibold leading-snug">{n.title}</span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {n.kind === "incident_proposed" ? <>
                <Button variant="primary" disabled={busy === n.id} onClick={() => act(n.id, "confirm", `/p/${pid}/i/${n.id}`)}>{n.action}</Button>
                <Button disabled={busy === n.id} onClick={() => act(n.id, "dismiss")}>Dismiss</Button>
              </> : (
                <Link href={n.kind.startsWith("incident") ? `/p/${pid}/i/${n.id}` : `/p/${pid}/r/${n.id}${n.kind === "tests" ? "?tab=testing" : ""}`}
                  className="inline-flex h-10 items-center rounded-lg bg-accent px-4 text-[13px] font-semibold text-accent-ink active:scale-[0.98]">{n.action}</Link>
              )}
              {n.detail && <span className="pl-1.5 text-[13px] text-muted">{n.detail}</span>}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export function ReleaseCard({ pid, now, others, lead, onSchedule }: {
  pid: string; now: NowData; others: { id: string; name: string; stage: string }[]; lead: boolean; onSchedule: () => void;
}) {
  const r = now.release;
  const stats = r ? [
    { n: r.agents_on, label: "agents are working on" },
    { n: r.waiting_owner, label: "waiting for an owner" },
    { n: r.fixing, label: "being fixed" },
    { n: r.verify, label: "waiting to be verified" },
  ].filter((s) => s.n > 0) : [];
  const idx = r ? STAGES.indexOf(r.stage) : -1;
  const synced = now.synced ? new Date(now.synced * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;
  return (
    <div className="flex flex-col gap-3.5">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[15px] font-semibold">Releases</h2>
        {lead && <button type="button" onClick={onSchedule} className="text-[13px] text-muted hover:text-text">Schedule a release</button>}
      </div>
      {r ? (
        <Link href={`/p/${pid}/r/${r.id}`} className="flex flex-col gap-4 rounded-2xl border border-line px-5 py-5 transition-colors hover:border-line-strong">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[17px] font-semibold">{r.name}</span>
            <span className="text-[13px] text-muted">{r.window ? `Deploys ${r.window.replace("T", " ")}` : "No deploy window yet"}</span>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            {/* Stages done are green, the one it's in takes its own colour, the rest stay grey. */}
            {STAGES.map((s, i) => (
              <span key={s} className="flex items-center gap-2">
                {i === idx && <span className={`h-1.5 w-1.5 rounded-full ${STAGE_TONE[s] ?? "bg-text"}`} />}
                <span className={i < idx ? "text-ok/70" : i === idx ? `font-semibold ${STAGE_TEXT[s] ?? "text-text"}` : ""}>{STAGE_WORD[s]}</span>
                {i < STAGES.length - 1 && <span className={`h-px w-7 ${i < idx ? "bg-ok/30" : "bg-line"}`} />}
              </span>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {stats.map((s) => (
              <div key={s.label} className="flex flex-col gap-0.5"><span className="font-mono text-[22px]">{s.n}</span><span className="text-xs text-muted">{s.label}</span></div>
            ))}
            {r.risks_high > 0 && (
              <div className="flex flex-col gap-1">
                <span className={`font-mono text-[22px] ${r.risks_tested === r.risks_high ? "text-ok" : "text-accent"}`}>{r.risks_tested}<span className="text-sm text-muted"> of {r.risks_high}</span></span>
                <span className="text-xs text-muted">high risks tested</span>
                <span className="mt-0.5 flex h-1 w-full max-w-[120px] gap-px overflow-hidden rounded-full" aria-hidden>
                  {Array.from({ length: r.risks_high }, (_, i) => (
                    <span key={i} className={`h-full flex-1 rounded-full ${i < r.risks_tested ? "bg-ok" : "bg-accent/35"}`} />
                  ))}
                </span>
              </div>
            )}
            {stats.length === 0 && r.risks_high === 0 && <span className="text-sm text-muted">Nothing open.</span>}
          </div>
        </Link>
      ) : <p className="rounded-2xl border border-dashed border-line-strong px-5 py-6 text-sm text-muted">No release in progress.</p>}
      {others.length > 0 && (
        <p className="text-[13px] text-muted">Also: {others.map((o, i) => <span key={o.id}>{i > 0 && ", "}<Link href={`/p/${pid}/r/${o.id}`} className="hover:text-text">{o.name}</Link> ({STAGE_WORD[o.stage]?.toLowerCase()})</span>)}</p>
      )}
      {synced && (
        <p className="flex items-center gap-2 text-[13px] text-muted">
          <Check size={14} className="text-ok" />
          Context synced at {synced} from {[now.counts.pr && `${now.counts.pr} PRs`, now.counts.file && `${now.counts.file} files`, now.counts.person && `${now.counts.person} people`].filter(Boolean).join(", ")}.
        </p>
      )}
    </div>
  );
}

/**
 * Each person's day against the 12h cap. Drag a waiting bug onto someone (or pick it, then pick them)
 * and their bar fills with its estimate before you commit.
 */
export function TeamToday({ team, queue, canAssign, onAssign }: {
  team: TeamRow[]; queue: QueueBug[]; canAssign: boolean; onAssign: (bug: QueueBug, to: TeamRow) => Promise<void>;
}) {
  const [held, setHeld] = useState<QueueBug | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [pending, setPending] = useState<Record<string, number>>({});
  const drop = async (row: TeamRow) => {
    const bug = held;
    setOver(null); setHeld(null);
    if (!bug || bug.proposed_id === row.id) return;
    setPending((p) => ({ ...p, [row.id]: (p[row.id] ?? 0) + bug.hours }));
    try { await onAssign(bug, row); } finally { setPending((p) => { const n = { ...p }; delete n[row.id]; return n; }); }
  };
  const busy = team.filter((t) => t.worked || t.agreed || t.proposed || pending[t.id]);
  const free = team.filter((t) => !busy.includes(t));
  const showAll = !!held;
  const rows = showAll ? team : busy;

  return (
    <div className="flex flex-col gap-3.5">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[15px] font-semibold">Team today</h2>
        <span className="text-xs text-muted">hours of {team[0]?.cap ?? 12}</span>
      </div>
      {canAssign && queue.length > 0 && (
        <div className="flex flex-col gap-2 rounded-xl border border-dashed border-line-strong p-3">
          <p className="text-xs text-muted">{held ? `Now drop it on a person, or pick them below.` : `Drag a bug onto someone to hand it to them.`}</p>
          <div className="flex flex-wrap gap-1.5">
            {queue.map((b) => (
              <button key={b.id} type="button" draggable onDragStart={(e) => { e.dataTransfer.setData("text/plain", b.id); setHeld(b); }} onDragEnd={() => { setOver(null); setTimeout(() => setHeld(null), 50); }}
                onClick={() => setHeld(held?.id === b.id ? null : b)} aria-pressed={held?.id === b.id}
                className={`flex max-w-full cursor-grab items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left text-xs active:cursor-grabbing ${held?.id === b.id ? "border-accent text-text" : "border-line-strong text-text-2 hover:bg-raised"}`}>
                <span className="truncate">{b.title}</span><span className="shrink-0 font-mono text-[10px] text-muted">~{b.hours}h</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="flex flex-col gap-3.5">
        {rows.map((t) => {
          const hover = over === t.id && held;
          const extra = (pending[t.id] ?? 0) + (hover ? held!.hours : 0);
          const proposed = t.proposed + extra;
          const used = t.worked + t.agreed + proposed;
          const left = +(t.cap - used).toFixed(1);
          const pct = (h: number) => `${(Math.min(h, t.cap) / t.cap) * 100}%`;
          const label = !used ? "free all day" : left < 0 ? `${-left}h over the cap` : proposed ? `${left}h left if approved` : `${left}h left`;
          return (
            <div key={t.id} onDragOver={(e) => { if (held) { e.preventDefault(); setOver(t.id); } }} onDragLeave={() => setOver((o) => (o === t.id ? null : o))}
              onDrop={(e) => { e.preventDefault(); drop(t); }}
              className={`-mx-2 flex flex-col gap-1.5 rounded-lg px-2 py-1.5 transition-colors ${hover ? "bg-raised" : ""}`}>
              <div className="flex items-center justify-between gap-3 text-[13px]">
                <span className={t.shift === "on" ? "" : "text-muted"}>{t.name}{t.shift !== "on" ? " (off shift)" : ""}</span>
                <span className="flex items-center gap-2">
                  <span className={`font-mono text-xs ${left < 0 ? "text-bad" : "text-muted"}`}>{label}</span>
                  {held && !hover && <button type="button" onClick={() => drop(t)} className="rounded border border-line-strong px-1.5 text-[11px] text-text-2 hover:bg-raised">here</button>}
                </span>
              </div>
              <div className="relative h-1.5 overflow-hidden rounded-full bg-[#1c1d21]">
                <div className="absolute inset-y-0 left-0 bg-[#4a4c52] transition-[width] duration-300" style={{ width: pct(t.worked + t.agreed) }} />
                {proposed > 0 && <div className="absolute inset-y-0 rounded-full border border-dashed border-accent transition-[left,width] duration-300"
                  style={{ left: pct(t.worked + t.agreed), width: pct(proposed) }} />}
              </div>
            </div>
          );
        })}
        {!showAll && free.length > 0 && (
          <div className="flex items-center justify-between gap-3 text-[13px] text-muted">
            <span className="truncate">{free.map((f) => f.name).join(", ")}</span>
            <span className="shrink-0 font-mono text-xs">{free.length > 1 ? `${free.length} free all day` : "free all day"}</span>
          </div>
        )}
      </div>
    </div>
  );
}

type FeedItem = { seq: number; ts: number; actor: string; agent: boolean; text: string; detail: string };

/** The one live thing on the page: the latest thing an agent or a person did. */
export function LiveLine({ pid }: { pid: string }) {
  const { data } = usePoll<FeedItem[]>(`/projects/${pid}/feed`, 3000);
  const [, tick] = useState(0);
  const first = useRef<number | null>(null);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 15000); return () => clearInterval(t); }, []);
  const last = data?.[0];
  if (!last) return null;
  if (first.current === null) first.current = last.seq;
  const fresh = last.seq !== first.current;
  return (
    <p key={last.seq} className={`flex min-w-0 items-center gap-2 text-[13px] text-muted ${fresh ? "arrive" : ""}`} aria-live="polite">
      {/* The live dot takes the colour of whoever moved last: violet for an agent, green for a person. */}
      <span className="relative flex h-2 w-2 shrink-0">
        <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-40 ${last.agent ? "bg-agent" : "bg-ok"}`} />
        <span className={`relative inline-flex h-2 w-2 rounded-full ${last.agent ? "bg-agent" : "bg-ok"}`} />
      </span>
      {last.agent ? <Robot size={13} className="shrink-0 text-agent" /> : <User size={13} className="shrink-0 text-ok" />}
      <span className="truncate"><span className="text-text-2">{last.actor}</span> {last.text}{last.detail ? `: ${last.detail}` : ""}</span>
      <span className="shrink-0 font-mono text-[11px] text-faint">{ago(last.ts)}</span>
    </p>
  );
}

export function ago(ts: number) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

type Scope = { value: string; open: number; total: number };

export function ScheduleRelease({ pid, onDone }: { pid: string; onDone: () => void }) {
  const [f, setF] = useState({ name: "", window_start: "", window_end: "", scope_type: "milestone", scope_value: "", migration: false });
  const [err, setErr] = useState<string | null>(null);
  const [scopes, setScopes] = useState<Record<string, Scope[]> | null>(null);
  useEffect(() => { get(`/projects/${pid}/releases/scopes`).then(setScopes).catch(() => setScopes({})); }, [pid]);

  const options = scopes?.[f.scope_type] ?? null;  // null: free text (PR numbers, all open)
  const match = options?.find((o) => o.value === f.scope_value.trim());
  const create = async () => {
    if (!f.name.trim()) return setErr("Name the release.");
    if (options && f.scope_value.trim() && !match) return setErr(`No PR has the ${f.scope_type} "${f.scope_value.trim()}". Pick one below.`);
    if (f.window_start && f.window_end && f.window_end <= f.window_start) return setErr("The window has to end after it starts.");
    try { await post(`/projects/${pid}/releases`, f); onDone(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const pick = (o: Scope) => { setErr(null); setF({ ...f, scope_value: o.value, name: f.name || (f.scope_type === "milestone" ? o.value : f.name) }); };

  return (
    <div className="enter grid gap-4 rounded-2xl border border-line bg-panel p-5 md:grid-cols-2">
      <Field label="Name"><input className={inputCls} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Checkout v3" /></Field>
      <Field label="Scope" hint={f.scope_type === "prs" ? "Comma-separated PR numbers." : f.scope_type === "open" ? "Every open PR in the repo." : "PRs in scope are linked automatically as they're opened."}>
        <div className="flex gap-2">
          <select aria-label="Scope by" className={`${inputCls} w-36`} value={f.scope_type} onChange={(e) => setF({ ...f, scope_type: e.target.value, scope_value: "" })}>
            <option value="milestone">Milestone</option><option value="label">Label</option><option value="branch">Branch</option>
            <option value="prs">PR numbers</option><option value="open">All open PRs</option>
          </select>
          {f.scope_type !== "open" && (
            <input aria-label="Scope value" className={`${inputCls} min-w-0 flex-1`} value={f.scope_value} onChange={(e) => setF({ ...f, scope_value: e.target.value })}
              placeholder={f.scope_type === "prs" ? "12, 15, 19" : options?.[0]?.value ?? ""} />
          )}
        </div>
      </Field>
      {options && options.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 md:col-span-2">
          {options.slice(0, 8).map((o) => (
            <button key={o.value} type="button" onClick={() => pick(o)}
              className={`inline-flex h-8 items-center gap-2 rounded-full border px-3 text-[13px] transition ${o.value === f.scope_value.trim() ? "border-text-2 bg-raised text-text" : "border-line text-text-2 hover:border-line-strong hover:text-text"}`}>
              {o.value}<span className="text-faint">{o.open ? `${o.open} open` : `${o.total} merged`}</span>
            </button>
          ))}
        </div>
      )}
      {options && options.length === 0 && <p className="text-xs text-muted md:col-span-2">No PR in the synced repos has a {f.scope_type} yet.</p>}
      <Field label="Deploy window start"><input type="datetime-local" className={inputCls} value={f.window_start} onChange={(e) => setF({ ...f, window_start: e.target.value })} /></Field>
      <Field label="Deploy window end"><input type="datetime-local" className={inputCls} value={f.window_end} min={f.window_start || undefined} onChange={(e) => setF({ ...f, window_end: e.target.value })} /></Field>
      <label className="flex items-center gap-2 text-sm text-text-2"><input type="checkbox" checked={f.migration} onChange={(e) => setF({ ...f, migration: e.target.checked })} /> Includes a data migration</label>
      <div className="flex items-center gap-3 md:justify-end">
        {err ? <span role="alert" className="text-xs text-bad">{err}</span>
          : match && <span className="text-xs text-muted">{match.open} open PR{match.open === 1 ? "" : "s"} in scope{match.total > match.open ? `, ${match.total - match.open} already merged` : ""}</span>}
        <Button onClick={onDone}>Cancel</Button>
        <Button variant="primary" onClick={create}>Schedule</Button>
      </div>
    </div>
  );
}
