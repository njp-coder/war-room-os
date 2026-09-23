"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, CaretLeft, CaretRight, Database, Eye, FileText, GitBranch, Globe, Pulse, Users } from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import { post, usePoll } from "@/lib/api";
import { BriefQ, Inline, SetupItem } from "./setup";

// ---------------- capability map ----------------

type Src = { id: string; label: string; icon: Icon; anchor: string };
type Cap = { id: string; label: string; needs: string[][] }; // any one inner list, all of its items

const SOURCES: Src[] = [
  { id: "repo", label: "Code and history", icon: GitBranch, anchor: "connect" },
  { id: "people", label: "People", icon: Users, anchor: "team" },
  { id: "brief", label: "How it's used", icon: FileText, anchor: "teach" },
  { id: "staging_db", label: "Staging database", icon: Database, anchor: "connect" },
  { id: "staging_url", label: "Staging app", icon: Globe, anchor: "connect" },
  { id: "monitor_db", label: "Production database", icon: Pulse, anchor: "connect" },
  { id: "sources", label: "Logs and errors", icon: Eye, anchor: "connect" },
];
const CAPS: Cap[] = [
  { id: "cause", label: "Find the cause in code", needs: [["repo"]] },
  { id: "route", label: "Route fixes to who knows it", needs: [["repo", "people"]] },
  { id: "repro", label: "Check data, reproduce bugs", needs: [["staging_db"]] },
  { id: "explore", label: "Run the app on staging", needs: [["staging_url", "staging_db"]] },
  { id: "warroom", label: "Open war rooms on its own", needs: [["monitor_db"], ["sources"]] },
  { id: "watch", label: "Watch releases after go-live", needs: [["repo", "monitor_db"], ["repo", "sources"]] },
  { id: "judge", label: "Judge like your team would", needs: [["brief"]] },
];

const W = 760, ROW = 44, TOP = 18, NODE_H = 32, LX = 0, LW = 210, RX = 470, RW = 290;

/** Sources wire into what agents can do. A wire lights up once its source is connected; dark sources are the to-do list. */
export function CapabilityMap({ items, onJump }: { items: SetupItem[]; onJump: (anchor: string) => void }) {
  const done = new Set(items.filter((i) => i.done).map((i) => i.id));
  const capOn = (c: Cap) => c.needs.some((set) => set.every((s) => done.has(s)));
  const H = TOP * 2 + Math.max(SOURCES.length, CAPS.length) * ROW;
  const sy = (i: number) => TOP + i * ROW + (H - TOP * 2 - SOURCES.length * ROW) / 2;
  const cy = (i: number) => TOP + i * ROW + (H - TOP * 2 - CAPS.length * ROW) / 2;
  // remember what was lit on the previous render, so only newly lit wires animate
  const prev = useRef<Set<string> | null>(null);
  const [first, setFirst] = useState(true);
  useEffect(() => { const t = setTimeout(() => setFirst(false), 1200); return () => clearTimeout(t); }, []);
  const wires: { key: string; d: string; on: boolean }[] = [];
  CAPS.forEach((c, ci) => {
    const srcs = [...new Set(c.needs.flat())];
    const on = capOn(c);
    srcs.forEach((s) => {
      const si = SOURCES.findIndex((x) => x.id === s);
      const x1 = LX + LW, y1 = sy(si) + NODE_H / 2, x2 = RX, y2 = cy(ci) + NODE_H / 2, mx = (x1 + x2) / 2;
      wires.push({ key: `${s}>${c.id}`, d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, on: on && done.has(s) });
    });
  });
  const lit = new Set(wires.filter((w) => w.on).map((w) => w.key));
  const fresh = (k: string) => lit.has(k) && (first || (prev.current !== null && !prev.current.has(k)));
  useEffect(() => { prev.current = lit; });
  const ready = CAPS.filter(capOn).length;

  return (
    <section aria-label="What agents can do with what is connected" className="flex flex-col gap-3 rounded-2xl border border-line bg-[#111214] px-5 py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-[15px] font-semibold">What agents can do</h2>
        <span className="font-mono text-xs text-muted">{ready} of {CAPS.length} working</span>
      </div>
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ minWidth: 620 }} role="img" aria-label={`${ready} of ${CAPS.length} capabilities working`}>
          {wires.filter((w) => !w.on).map((w) => <path key={w.key} d={w.d} fill="none" stroke="var(--line-strong)" strokeWidth="1" strokeDasharray="3 5" />)}
          {/* A live wire is green like the dot on the source it comes from; unconnected ones stay dashed and grey. */}
          {wires.filter((w) => w.on).map((w) => <path key={w.key} d={w.d} fill="none" stroke="var(--ok)" strokeOpacity="0.5" strokeWidth="1.5" className={fresh(w.key) ? "trace" : ""} />)}
          {SOURCES.map((s, i) => {
            const on = done.has(s.id);
            const Ico = s.icon;
            return (
              <g key={s.id} transform={`translate(${LX},${sy(i)})`} style={{ cursor: on ? "default" : "pointer" }} onClick={() => !on && onJump(s.anchor)}
                role={on ? undefined : "button"} aria-label={on ? `${s.label}: connected` : `${s.label}: not connected, go to set it up`}>
                <rect width={LW} height={NODE_H} rx={8} fill={on ? "var(--raised)" : "var(--bg)"} stroke={on ? "var(--line-strong)" : "var(--line)"} />
                <foreignObject x={10} y={8} width={16} height={16}><Ico size={16} color={on ? "var(--text)" : "var(--muted)"} /></foreignObject>
                <text x={34} y={NODE_H / 2 + 4} fontSize="12.5" fill={on ? "var(--text)" : "var(--muted)"}>{s.label}</text>
                {on ? <circle cx={LW - 14} cy={NODE_H / 2} r={3.5} fill="var(--ok)" /> : <text x={LW - 10} y={NODE_H / 2 + 4} fontSize="11" textAnchor="end" fill="var(--text-2)">Connect</text>}
              </g>
            );
          })}
          {CAPS.map((c, i) => {
            const on = capOn(c);
            return (
              <g key={c.id} transform={`translate(${RX},${cy(i)})`}>
                <rect width={RW} height={NODE_H} rx={NODE_H / 2} fill={on ? "var(--raised)" : "transparent"} stroke={on ? "var(--ok)" : "var(--line)"} strokeOpacity={on ? 0.45 : 1} strokeDasharray={on ? undefined : "3 4"} />
                <text x={16} y={NODE_H / 2 + 4} fontSize="12.5" fill={on ? "var(--text)" : "var(--faint)"}>{c.label}</text>
              </g>
            );
          })}
        </svg>
      </div>
    </section>
  );
}

// ---------------- connection tiles ----------------

const TILE: Record<string, { icon: Icon; name: string }> = {
  repo: { icon: GitBranch, name: "Code and history" },
  staging_db: { icon: Database, name: "Staging database" },
  staging_url: { icon: Globe, name: "Staging app" },
  monitor_db: { icon: Pulse, name: "Production database" },
  sources: { icon: Eye, name: "Logs and errors" },
};

/** One tile per connection: what it is, why it matters, and either its status or the action to connect it. */
export function ConnectTiles({ pid, items, lead, reload, onJump }: { pid: string; items: SetupItem[]; lead: boolean; reload: () => void; onJump: (a: string) => void }) {
  const tiles = Object.keys(TILE).map((id) => items.find((i) => i.id === id)).filter(Boolean) as SetupItem[];
  const todo = tiles.filter((t) => !t.done), done = tiles.filter((t) => t.done);
  return (
    <div className="flex flex-col gap-3">
      {todo.length > 0 && (
        <div className="grid gap-3 lg:grid-cols-2">
          {todo.map((t) => {
            const { icon: Ico, name } = TILE[t.id];
            return (
              <div key={t.id} className="flex flex-col gap-3 rounded-2xl border border-line bg-[#111214] p-5">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-raised text-text-2"><Ico size={16} /></span>
                  <span className="text-[15px] font-medium">{name}</span>
                </div>
                <p className="text-[13px] leading-relaxed text-muted">{t.why}</p>
                <div className="mt-auto">
                  {t.id === "sources"
                    ? <button type="button" onClick={() => onJump("logs")} className="flex items-center gap-1.5 text-[13px] text-text-2 hover:text-text">Choose Sentry, Datadog, Loki or a log file <ArrowRight size={12} /></button>
                    : lead ? <Inline pid={pid} item={t} reload={reload} /> : <span className="text-xs text-muted">A lead connects this.</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {done.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {done.map((t) => {
            const { icon: Ico, name } = TILE[t.id];
            return (
              <span key={t.id} className="flex max-w-full items-center gap-2 rounded-full border border-line px-3 py-1.5 text-[13px]" title={t.detail}>
                <Ico size={14} className="text-text-2" />{name}<Check size={12} className="text-ok" />
                {t.detail && <span className="max-w-[240px] truncate font-mono text-[11px] text-muted">{t.detail}</span>}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------- brief as an interview ----------------

/** One question at a time. Answers save as you go; answered ones collapse into a list you can reopen. */
export function BriefInterview({ pid }: { pid: string }) {
  const { data, reload } = usePoll<{ questions: BriefQ[]; answered: number; can_answer: boolean }>(`/projects/${pid}/brief`, 60000);
  const [idx, setIdx] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const qs = data?.questions ?? [];
  const firstOpen = qs.findIndex((q) => !q.answer);
  const i = idx ?? (firstOpen >= 0 ? firstOpen : 0);
  const q = qs[i];
  useEffect(() => { setText(q?.answer ?? ""); }, [q?.qid]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!data || !q) return <div className="h-56 animate-pulse rounded-2xl bg-panel" />;
  const picked = new Set((q.answer ?? "").split(",").map((x) => x.trim()).filter(Boolean));
  const save = async (value: string, next = true) => {
    setSaving(true);
    if (value.trim() !== (q.answer ?? "").trim()) await post(`/projects/${pid}/brief/${q.qid}`, { answer: value });
    setSaving(false);
    reload();
    if (next) {
      const after = qs.findIndex((x, j) => j > i && !x.answer);
      setIdx(after >= 0 ? after : Math.min(i + 1, qs.length - 1));
    }
  };
  const fromCode = q.kind !== "core";
  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-1" role="tablist" aria-label="Questions">
        {qs.map((x, j) => (
          <button key={x.qid} type="button" role="tab" aria-selected={j === i} aria-label={`Question ${j + 1}${x.answer ? ", answered" : ""}`} onClick={() => setIdx(j)}
            className={`h-1.5 flex-1 rounded-full transition-colors ${j === i ? "bg-text" : x.answer ? "bg-ok/70" : "bg-line-strong hover:bg-muted"}`} />
        ))}
      </div>
      <div key={q.qid} className="enter flex flex-col gap-4 rounded-2xl border border-line bg-[#111214] px-6 py-6">
        <div className="flex items-baseline justify-between gap-3 text-xs text-muted">
          <span>{fromCode ? "Asked because of what's in your code" : "About the product"}</span>
          <span className="font-mono">{i + 1} of {qs.length}</span>
        </div>
        <label htmlFor="brief-q" className="max-w-[40ch] text-[22px] font-semibold leading-snug tracking-tight">{q.question}</label>
        {q.choices.length ? (
          <div className="flex flex-wrap gap-1.5" role="group" aria-label={q.question}>
            {q.choices.map((c) => {
              const on = picked.has(c);
              return (
                <button key={c} type="button" disabled={!data.can_answer} aria-pressed={on}
                  onClick={() => { const n = new Set(picked); if (on) n.delete(c); else n.add(c); save([...n].join(", "), false); }}
                  className={`rounded-md border px-2.5 py-1.5 font-mono text-[11.5px] transition-colors ${on ? "border-text-2 bg-raised text-text" : "border-line-strong text-muted hover:text-text"}`}>
                  {on && <Check size={10} className="mr-1 inline" />}{c}
                </button>
              );
            })}
          </div>
        ) : (
          <textarea id="brief-q" rows={3} disabled={!data.can_answer} value={text} onChange={(e) => setText(e.target.value)} placeholder={q.hint}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(text); }}
            className="rounded-xl border border-line-strong bg-bg px-4 py-3 text-[15px] leading-relaxed placeholder:text-faint focus:border-text-2 focus:outline-none" />
        )}
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" aria-label="Previous question" disabled={i === 0} onClick={() => setIdx(i - 1)} className="flex h-9 w-9 items-center justify-center rounded-lg border border-line-strong text-muted hover:text-text disabled:opacity-30"><CaretLeft size={14} /></button>
          <button type="button" aria-label="Next question" disabled={i === qs.length - 1} onClick={() => setIdx(i + 1)} className="flex h-9 w-9 items-center justify-center rounded-lg border border-line-strong text-muted hover:text-text disabled:opacity-30"><CaretRight size={14} /></button>
          {!q.choices.length && data.can_answer && (
            <button type="button" disabled={saving || !text.trim()} onClick={() => save(text)}
              className="ml-auto inline-flex h-9 items-center gap-1.5 rounded-lg bg-[#2b2d33] px-4 text-[13px] font-medium text-text hover:bg-line-strong active:scale-[0.98] disabled:opacity-40">
              {saving ? "Saving" : "Save and next"}<span className="font-mono text-[10px] text-muted">⌘↵</span>
            </button>
          )}
          {q.answered_by && <span className="text-xs text-muted">Answered by {q.answered_by}</span>}
        </div>
      </div>
      <p className="text-xs text-muted">{data.answered} of {qs.length} answered. Agents use these when deciding if a signal is real and when explaining a bug.</p>
    </div>
  );
}

// ---------------- section rail ----------------

export type RailItem = { id: string; label: string; count?: string; done?: boolean };

/** Sticky chapter list. Highlights the section in view. */
export function Rail({ items }: { items: RailItem[] }) {
  const [active, setActive] = useState(items[0]?.id);
  useEffect(() => {
    const els = items.map((i) => document.getElementById(i.id)).filter(Boolean) as HTMLElement[];
    // Whenever any section crosses a line near the top, the active one is the last whose top has passed that line.
    const pick = () => {
      const passed = els.filter((e) => e.getBoundingClientRect().top <= 140);
      setActive((passed[passed.length - 1] ?? els[0])?.id);
    };
    const io = new IntersectionObserver(pick, { rootMargin: "-140px 0px -40% 0px", threshold: [0, 1] });
    els.forEach((e) => io.observe(e));
    pick();
    return () => io.disconnect();
  }, [items.map((i) => i.id).join()]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <nav aria-label="Setup sections" className="sticky top-20 hidden flex-col gap-0.5 self-start lg:flex">
      {items.map((i) => (
        <a key={i.id} href={`#${i.id}`} aria-current={active === i.id ? "true" : undefined}
          className={`flex items-center justify-between gap-3 rounded-lg px-3 py-2 text-[13px] transition-colors ${active === i.id ? "bg-raised text-text" : "text-muted hover:text-text"}`}>
          <span>{i.label}</span>
          {i.done ? <Check size={12} className="text-ok" /> : i.count ? <span className="font-mono text-[11px] text-faint">{i.count}</span> : null}
        </a>
      ))}
    </nav>
  );
}

export function Chapter({ id, title, lead, children }: { id: string; title: string; lead: string; children: React.ReactNode }) {
  return (
    <section id={id} className="flex scroll-mt-20 flex-col gap-5">
      <div className="flex flex-col gap-1.5">
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        <p className="max-w-[70ch] text-[14px] leading-relaxed text-muted">{lead}</p>
      </div>
      {children}
    </section>
  );
}
