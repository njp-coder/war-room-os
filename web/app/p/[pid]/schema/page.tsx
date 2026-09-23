"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Key, LinkSimple, Warning } from "@phosphor-icons/react";
import { usePoll } from "@/lib/api";
import { Shell } from "@/components/shell";

type Col = { name: string; type: string | null; required: boolean; pk: boolean; fk: string | null; reads: number; writes: number;
  live: { present: boolean; nullable?: boolean; type?: string; only_live?: boolean } | null; failing: string | null };
type Table = { name: string; source: string | null; live_name: string | null; columns: Col[]; readers: string[]; writers: string[]; endpoints: string[] };
type S = { tables: Table[]; relations: { from: string; to: string; live?: boolean }[]; live_connected: boolean; only_live: string[] };

const key = (s: string) => s.toLowerCase().replace(/s$/, "");

export default function Schema() {
  const { pid } = useParams<{ pid: string }>();
  const { data: p } = usePoll<{ project: { name: string } }>(`/projects/${pid}`, 60000);
  const { data: s } = usePoll<S>(`/projects/${pid}/schema`, 15000);
  const [sel, setSel] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [paths, setPaths] = useState<{ d: string; hot: boolean; live?: boolean }[]>([]);

  // Most-connected tables first, so related tables sit near each other.
  const tables = [...(s?.tables ?? [])].sort((a, b) => degree(b, s) - degree(a, s));

  const draw = useCallback(() => {
    if (!box.current || !s) return;
    const root = box.current.getBoundingClientRect();
    const out: { d: string; hot: boolean; live?: boolean }[] = [];
    for (const r of s.relations) {
      const [ft, fc] = r.from.split("."), [tt, tc] = r.to.split(".");
      const a = box.current.querySelector(`[data-col="${key(ft)}.${fc.toLowerCase()}"]`);
      const b = box.current.querySelector(`[data-col="${key(tt)}.${tc.toLowerCase()}"]`) ?? box.current.querySelector(`[data-table="${key(tt)}"]`);
      if (!a || !b) continue;
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      const leftToRight = ra.left < rb.left;
      const x1 = (leftToRight ? ra.right : ra.left) - root.left, y1 = ra.top + ra.height / 2 - root.top;
      const x2 = (leftToRight ? rb.left : rb.right) - root.left, y2 = rb.top + Math.min(rb.height, 34) / 2 - root.top;
      const bend = Math.max(40, Math.abs(x2 - x1) / 2) * (leftToRight ? 1 : -1);
      const hot = !!sel && (key(ft) === key(sel) || key(tt) === key(sel)) || (!!hover && (hover === r.from || hover === r.to));
      out.push({ d: `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`, hot, live: r.live });
    }
    setPaths(out);
  }, [s, sel, hover]);

  useLayoutEffect(() => { const t = setTimeout(draw, 0); return () => clearTimeout(t); }, [draw]);
  useEffect(() => { window.addEventListener("resize", draw); return () => window.removeEventListener("resize", draw); }, [draw]);

  const selected = tables.find((t) => t.name === sel);
  return (
    <Shell wide crumbs={[{ label: p?.project.name ?? "Project", href: `/p/${pid}` }, { label: "Schema" }]}>
      <div className="flex flex-col gap-6">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">Schema</h1>
            <p className="max-w-[70ch] text-sm text-muted">Tables from your code, matched against the live staging database. Lines are relationships. Click a table to see the code and endpoints that use it.</p>
          </div>
          <Legend live={s?.live_connected} />
        </header>
        {!s && <div className="h-80 animate-pulse rounded-xl bg-panel" />}
        {s && s.tables.length === 0 && <p className="rounded-xl border border-dashed border-line-strong p-8 text-sm text-muted">No tables found in the code yet. The context pipeline reads SQL files, migrations, Prisma schemas and ORM models.</p>}
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div ref={box} className="relative overflow-x-auto rounded-xl border border-line bg-panel/40 p-6">
            <svg className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">
              {paths.map((pth, i) => <path key={i} d={pth.d} fill="none" stroke={pth.hot ? "var(--accent)" : "var(--line-strong)"}
                strokeWidth={pth.hot ? 2 : 1.25} strokeDasharray={pth.live ? "4 4" : undefined} />)}
            </svg>
            <div className="relative grid gap-x-16 gap-y-8" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))" }}>
              {tables.map((t) => (
                <TableCard key={t.name} t={t} active={sel === t.name} onClick={() => setSel(sel === t.name ? null : t.name)} onHover={setHover} />
              ))}
            </div>
            {s && s.only_live.length > 0 && (
              <p className="relative mt-6 text-xs text-muted">Only in the database, not in your code: {s.only_live.join(", ")}</p>
            )}
          </div>
          <aside className="flex flex-col gap-4">
            {selected ? <TableDetail t={selected} pid={pid} /> : <p className="text-sm text-muted">Select a table to see who reads it, who writes it, and which endpoints reach it.</p>}
          </aside>
        </div>
      </div>
    </Shell>
  );
}

function degree(t: Table, s?: S | null) {
  if (!s) return 0;
  return s.relations.filter((r) => key(r.from.split(".")[0]) === key(t.name) || key(r.to.split(".")[0]) === key(t.name)).length * 10 + t.columns.length;
}

function TableCard({ t, active, onClick, onHover }: { t: Table; active: boolean; onClick: () => void; onHover: (c: string | null) => void }) {
  const bad = t.columns.filter((c) => c.failing).length;
  return (
    <button type="button" onClick={onClick} data-table={key(t.name)}
      className={`flex flex-col overflow-hidden rounded-xl border bg-bg text-left transition-colors ${active ? "border-accent" : bad ? "border-bad/60" : "border-line-strong hover:border-text-2"}`}>
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <span className="font-mono text-sm font-medium">{t.name}</span>
        <span className="flex items-center gap-2 text-[10px]">
          {bad > 0 && <span className="flex items-center gap-1 text-bad"><Warning size={11} />{bad}</span>}
          {t.live_name ? <span className="text-ok">live</span> : <span className="text-muted">code only</span>}
        </span>
      </div>
      <ul className="flex flex-col py-1">
        {t.columns.map((c) => (
          <li key={c.name} data-col={`${key(t.name)}.${c.name.toLowerCase()}`} onMouseEnter={() => c.fk && onHover(`${t.name}.${c.name}`)} onMouseLeave={() => onHover(null)}
            title={c.failing ?? (c.live && !c.live.present ? "Declared in code, missing in the database" : undefined)}
            className={`grid grid-cols-[14px_minmax(0,1fr)_auto] items-center gap-2 px-3 py-1 text-[12px] ${c.failing ? "bg-bad-soft/60" : ""} ${c.live?.only_live ? "opacity-50" : ""}`}>
            <span className="text-accent">{c.pk ? <Key size={11} /> : c.fk ? <LinkSimple size={11} className="text-text-2" /> : null}</span>
            <span className="flex min-w-0 items-center gap-1.5">
              <span className={`truncate font-mono ${c.failing ? "text-bad" : ""}`}>{c.name}</span>
              {c.required && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-text-2" title="Required in code" />}
              {c.live && !c.live.present && <span className="text-[9px] text-bad">missing in DB</span>}
            </span>
            <span className="flex items-center gap-1 font-mono text-[10px] text-muted" title={`${c.reads} functions read, ${c.writes} write`}>
              {c.reads + c.writes > 0 && <><span>{c.reads}r</span><span className={c.writes ? "text-accent" : ""}>{c.writes}w</span></>}
            </span>
          </li>
        ))}
      </ul>
    </button>
  );
}

function TableDetail({ t, pid }: { t: Table; pid: string }) {
  const bad = t.columns.filter((c) => c.failing);
  return (
    <div className="enter flex flex-col gap-5 rounded-xl border border-line p-4">
      <div>
        <h2 className="font-mono text-base">{t.name}</h2>
        <p className="text-xs text-muted">{t.source ? `Defined in ${t.source}` : "Found in the database"}{t.live_name ? `, live as ${t.live_name}` : ""}</p>
      </div>
      {bad.length > 0 && (
        <div className="flex flex-col gap-1.5 rounded-lg bg-bad-soft/60 p-3">
          {bad.map((c) => <p key={c.name} className="text-xs text-bad">{c.failing}</p>)}
        </div>
      )}
      <List title="Writes it" items={t.writers} tone="text-accent" />
      <List title="Reads it" items={t.readers} />
      <List title="Endpoints that reach it" items={t.endpoints} mono />
      <Link href={`/p/${pid}/context?node=${encodeURIComponent(`table:${t.name}`)}`} className="text-xs text-accent hover:underline">Explore everything connected to {t.name}</Link>
    </div>
  );
}

function List({ title, items, tone = "", mono = false }: { title: string; items: string[]; tone?: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="text-xs font-semibold text-muted">{title} ({items.length})</h3>
      <div className="flex flex-wrap gap-1.5">
        {items.length === 0 && <span className="text-xs text-faint">None found</span>}
        {items.map((i) => <span key={i} className={`rounded bg-raised px-1.5 py-0.5 text-[11px] ${mono ? "font-mono" : ""} ${tone}`}>{i}</span>)}
      </div>
    </div>
  );
}

function Legend({ live }: { live?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-4 text-[11px] text-muted">
      <span className="flex items-center gap-1"><Key size={11} className="text-accent" />primary key</span>
      <span className="flex items-center gap-1"><LinkSimple size={11} />foreign key</span>
      <span className="flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full bg-text-2" />required in code</span>
      <span><span className="font-mono">3r 1w</span> functions reading / writing</span>
      <span className="flex items-center gap-1"><span className="h-2 w-3 rounded-sm bg-bad-soft" />failing data check</span>
      {!live && <span className="text-accent">Connect a staging database to compare with live tables</span>}
    </div>
  );
}
