"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { ArrowLeft, ArrowRight, MagnifyingGlass } from "@phosphor-icons/react";
import { get, usePoll } from "@/lib/api";
import { Shell, inputCls } from "@/components/shell";
import { Initials } from "@/components/ui";
import { Observed } from "@/components/explorer";

type Person = { login: string; name: string; member: boolean; prs: number };
type Area = { id: string; name: string; endpoints: string[]; handlers: string[]; functions: number; files: number; tables: string[];
  writes: string[]; columns: number; prs: number; heat: number[]; people: Person[]; reviewers: string[]; single_owner: boolean;
  track: { id: string; name: string } | null;
  node_ids: { endpoints: string[]; handlers: string[]; tables: string[] } };
type Areas = { areas: Area[]; stats: { endpoints: number; functions: number; files: number; tables: number; columns: number; prs: number; people: number; solo_areas: string[] };
  gaps: { title: string; detail: string }[] };
type AskResult = { q: string; exact: boolean; answer: string; areas: string[];
  groups: { kind: string; items: { id: string; kind: string; title: string; sub: string; area: string | null }[] }[] };
type NodeView = { node: { id: string; type: string; label: string; props: Record<string, unknown>; text: string | null };
  groups: { dir: string; relation: string; type: string; count: number; items: { id: string; label: string; type: string }[] }[] };

// Area identity colours (never amber: amber means "you need to act").
const TINTS = [
  { tint: "#12211f", edge: "#2f5a52", ink: "#7fd1bf" }, { tint: "#141b27", edge: "#2e4466", ink: "#8fb3ff" },
  { tint: "#1d1626", edge: "#4a3766", ink: "#c3a6f2" }, { tint: "#18200f", edge: "#3d5227", ink: "#b5d98a" },
  { tint: "#10202a", edge: "#28506a", ink: "#86cbe6" }, { tint: "#241617", edge: "#5a3033", ink: "#f0a1a6" },
  { tint: "#1f1d14", edge: "#4d4a30", ink: "#d9d38a" }, { tint: "#17181b", edge: "#34363c", ink: "#c9c8c3" },
];

export default function ContextPage() {
  return <Suspense fallback={null}><Context /></Suspense>;
}

function Context() {
  const { pid } = useParams<{ pid: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const nodeId = params.get("node");
  const { data: p } = usePoll<{ project: { name: string }; role: string }>(`/projects/${pid}`, 60000);
  const { data: a } = usePoll<Areas>(`/projects/${pid}/context/areas`, 60000);
  const { data: view } = usePoll<NodeView>(nodeId ? `/projects/${pid}/graph/node?id=${encodeURIComponent(nodeId)}` : null, 60000);
  const [picked, setPicked] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [ans, setAns] = useState<AskResult | null>(null);
  const [asking, setAsking] = useState(false);
  const go = (id: string) => router.push(`/p/${pid}/context?node=${encodeURIComponent(id)}`);
  const search = async () => {
    if (!q.trim()) return;
    setAsking(true);
    try { setAns(await get(`/projects/${pid}/context/ask?q=${encodeURIComponent(q)}`)); } finally { setAsking(false); }
  };
  const name = p?.project.name ?? "Project";
  const areas = a?.areas ?? [];
  const sel = areas.find((x) => x.id === picked) ?? areas[0];
  const colour = (id: string) => TINTS[Math.max(0, areas.findIndex((x) => x.id === id)) % TINTS.length];

  return (
    <Shell wide crumbs={[{ label: name, href: `/p/${pid}` }, { label: "Context" }]}>
      <div className="flex flex-col gap-5">
        <div className="flex flex-wrap items-end gap-6">
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <h1 className="text-[30px] font-semibold tracking-tight">{name}, mapped</h1>
            <p className="max-w-[80ch] text-[15px] text-[#b8b7b2]">
              {areas.length ? `${areas.length} areas, sized by how much code they hold. Brighter bars mean more recent changes. Pick an area to trace it end to end.`
                : a ? "No areas yet. They appear once the repo sync finds HTTP endpoints." : " "}
            </p>
          </div>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); search(); }}>
            <label htmlFor="q" className="sr-only">Search context</label>
            <input id="q" className={`${inputCls} w-[340px] max-w-full`} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Where does checkout apply coupons?" />
            <button type="submit" disabled={asking} className="inline-flex h-10 items-center gap-2 rounded-lg border border-line-strong px-3.5 text-[13px] hover:bg-raised disabled:opacity-60"><MagnifyingGlass size={14} />{asking ? "Looking" : "Ask"}</button>
          </form>
        </div>

        {ans && <Answer ans={ans} colourOf={(n) => { const i = areas.findIndex((x) => x.name === n.split(" · ")[0]); return i >= 0 ? TINTS[i % TINTS.length] : null; }}
          go={go} close={() => setAns(null)} pick={(n) => { const x = areas.find((a) => a.name === n.split(" · ")[0]); if (x) { setPicked(x.id); setAns(null); } }} />}

        {nodeId && view ? <Explorer view={view} go={go} back={() => router.push(`/p/${pid}/context`)} /> : (
          <>
            {a?.stats && a.stats.endpoints > 0 && <Stats s={a.stats} />}
            {!a && <div className="h-[520px] animate-pulse rounded-2xl bg-panel" />}
            {sel && (
              <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
                <div className="flex flex-col gap-2">
                  <Treemap areas={areas} sel={sel.id} onPick={setPicked} colour={colour} />
                  {a!.gaps.map((g) => (
                    <div key={g.title} className="rounded-xl border border-dashed border-[#6b5324] px-[18px] py-3.5">
                      <p className="text-sm font-semibold text-accent">{g.title}</p>
                      <p className="text-xs leading-relaxed text-[#b8b7b2]">{g.detail}</p>
                    </div>
                  ))}
                </div>
                <Trace a={sel} ink={colour(sel.id)} pid={pid} go={go} />
              </div>
            )}
            {a && <Observed pid={pid} lead={p?.role === "owner" || p?.role === "lead"} />}
          </>
        )}
      </div>
    </Shell>
  );
}

const NAVIGABLE = /^(sym|route|field|table|pr|file):/;

/** Search results for people: a plain answer, then what matched, grouped and titled in words. */
function Answer({ ans, colourOf, go, close, pick }: { ans: AskResult; colourOf: (area: string) => { ink: string; tint: string } | null;
  go: (id: string) => void; close: () => void; pick: (area: string) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section className="enter flex flex-col gap-4 rounded-2xl border border-line bg-[#131417] px-6 py-5" aria-live="polite">
      <div className="flex items-start justify-between gap-4">
        <p className="max-w-[90ch] text-[15px] leading-relaxed">{ans.answer}</p>
        <button type="button" onClick={close} className="shrink-0 text-xs text-muted hover:text-text">Close</button>
      </div>
      {ans.areas.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span>Areas:</span>
          {ans.areas.map((a) => { const c = colourOf(a); return (
            <button key={a} type="button" onClick={() => pick(a)} className="rounded-md px-2 py-1 text-[12px]"
              style={{ background: c?.tint ?? "#1c1d21", color: c?.ink ?? "var(--text-2)" }}>{a}</button>
          ); })}
        </div>
      )}
      {!ans.exact && ans.groups.length > 0 && <p className="text-xs text-muted">Nothing matches those words exactly. These are the closest related things:</p>}
      <div className="grid gap-x-8 gap-y-4 md:grid-cols-2">
        {ans.groups.map((g) => (
          <div key={g.kind} className="flex flex-col gap-1.5">
            <h3 className="text-xs font-semibold text-text-2">{g.kind}</h3>
            <ul className="flex flex-col gap-1">
              {g.items.slice(0, 5).map((i) => (
                <li key={i.id}>
                  <button type="button" onClick={() => (NAVIGABLE.test(i.id) ? go(i.id) : setOpen(open === i.id ? null : i.id))}
                    className="flex w-full flex-col items-start gap-0.5 rounded-lg px-2 py-1.5 text-left hover:bg-raised">
                    <span className="flex w-full items-baseline gap-2">
                      <span className="min-w-0 truncate text-[13px]">{i.title}</span>
                      {i.area && <span className="shrink-0 text-[11px]" style={{ color: colourOf(i.area)?.ink ?? "var(--muted)" }}>{i.area}</span>}
                    </span>
                    {i.sub && <span className={`text-xs text-muted ${open === i.id ? "" : "line-clamp-1"}`}>{i.sub}</span>}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

function Stats({ s }: { s: Areas["stats"] }) {
  const items = [
    [s.endpoints, "endpoints"], [s.functions, "functions"], [s.files, "files"],
    [s.tables, s.columns ? `tables, ${s.columns} columns` : "tables"], [s.prs, "pull requests"],
    [s.people, s.people === 1 ? "person knows all of it" : "people have changed it"],
  ].filter(([n]) => Number(n) > 0) as [number, string][];
  return (
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
      {items.map(([n, label]) => (
        <div key={label} className="flex items-baseline gap-2 rounded-[10px] bg-[#131417] px-3.5 py-2.5">
          <span className="font-mono text-xl">{n}</span><span className="text-xs text-muted">{label}</span>
        </div>
      ))}
    </div>
  );
}

type Rect = { x: number; y: number; w: number; h: number };

/** Squarified treemap: each area's tile is proportional to its code, laid out to keep tiles close to square. */
function layout(sizes: number[], box: Rect): Rect[] {
  const total = sizes.reduce((s, v) => s + v, 0) || 1;
  const items = sizes.map((v, i) => ({ i, v: (v / total) * box.w * box.h }));
  const out: Rect[] = new Array(sizes.length);
  let rest = { ...box };
  let row: typeof items = [];
  const worst = (r: typeof items, side: number) => {
    const sum = r.reduce((s, x) => s + x.v, 0);
    const max = Math.max(...r.map((x) => x.v)), min = Math.min(...r.map((x) => x.v));
    return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
  };
  const place = (r: typeof items) => {
    const sum = r.reduce((s, x) => s + x.v, 0);
    if (rest.w >= rest.h) {
      const w = sum / rest.h;
      let y = rest.y;
      for (const x of r) { const h = x.v / w; out[x.i] = { x: rest.x, y, w, h }; y += h; }
      rest = { x: rest.x + w, y: rest.y, w: rest.w - w, h: rest.h };
    } else {
      const h = sum / rest.w;
      let x0 = rest.x;
      for (const x of r) { const w = x.v / h; out[x.i] = { x: x0, y: rest.y, w, h }; x0 += w; }
      rest = { x: rest.x, y: rest.y + h, w: rest.w, h: rest.h - h };
    }
  };
  for (const it of items) {
    const side = Math.min(rest.w, rest.h);
    if (!row.length || worst([...row, it], side) <= worst(row, side)) row.push(it);
    else { place(row); row = [it]; }
  }
  if (row.length) place(row);
  return out;
}

function Treemap({ areas, sel, onPick, colour }: { areas: Area[]; sel: string; onPick: (id: string) => void; colour: (id: string) => typeof TINTS[0] }) {
  // Sizes are code weight; a floor keeps tiny areas (health checks) clickable.
  const rects = useMemo(() => {
    const raw = areas.map((a) => a.functions + a.endpoints.length);
    const max = Math.max(...raw, 1);
    return layout(raw.map((v) => Math.max(v, max * 0.08)), { x: 0, y: 0, w: 100, h: 100 });
  }, [areas]);
  return (
    <div className="relative h-[560px] w-full" role="list" aria-label="Areas of the project">
      {areas.map((a, i) => {
        const r = rects[i], c = colour(a.id), on = a.id === sel;
        const big = r.w * r.h > 900, tiny = r.w * r.h < 250;
        return (
          <div key={a.id} role="listitem" className="absolute p-1" style={{ left: `${r.x}%`, top: `${r.y}%`, width: `${r.w}%`, height: `${r.h}%` }}>
            <button type="button" onClick={() => onPick(a.id)} aria-pressed={on}
              className="flex h-full w-full flex-col gap-2.5 overflow-hidden rounded-xl px-[18px] py-4 text-left transition-[border-color] duration-150"
              style={{ background: c.tint, border: on ? `2px solid ${c.ink}` : `1px solid ${c.edge}` }}>
              <div className="flex w-full flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
                <span className="font-semibold tracking-tight" style={{ color: c.ink, fontSize: big ? 26 : tiny ? 15 : 19 }}>{a.name}</span>
                {!tiny && <span className="text-xs text-[#b8b7b2]">{a.functions} function{a.functions === 1 ? "" : "s"}{a.tables.length ? `, ${a.tables.length} table${a.tables.length === 1 ? "" : "s"}` : ""}</span>}
                {a.track && !tiny && <span className="ml-auto rounded bg-black/30 px-1.5 py-0.5 text-[11px] text-[#d8d7d2]">{a.track.name}</span>}
              </div>
              {!tiny && (
                <div className="flex flex-wrap gap-1.5">
                  {a.endpoints.slice(0, big ? 4 : 2).map((e) => <span key={e} className="rounded bg-black/30 px-[7px] py-[3px] font-mono text-[11px] text-[#d8d7d2]">{e}</span>)}
                  {a.endpoints.length > (big ? 4 : 2) && <span className="px-1 font-mono text-[11px] text-[#b8b7b2]">+{a.endpoints.length - (big ? 4 : 2)}</span>}
                </div>
              )}
              <div className="flex-1" />
              <div className="flex w-full items-end gap-3">
                <Heat heat={a.heat} ink={c.ink} />
                <span className="flex shrink-0 -space-x-1.5">
                  {a.people.slice(0, 3).map((p) => <Initials key={p.login} name={p.name} size={24} />)}
                </span>
              </div>
            </button>
          </div>
        );
      })}
    </div>
  );
}

/** One bar per week, oldest first: taller and brighter means more merged changes that week. */
function Heat({ heat, ink }: { heat: number[]; ink: string }) {
  const max = Math.max(...heat, 1);
  const total = heat.reduce((s, n) => s + n, 0);
  return (
    <div className="flex h-[22px] flex-1 items-end gap-[2px]" title={`${total} merged change${total === 1 ? "" : "s"} in the last ${heat.length} weeks`}>
      {heat.map((n, i) => (
        <div key={i} className="flex-1 rounded-[1px]" style={{ height: `${Math.max(3, (n / max) * 22)}px`, background: ink, opacity: n ? Math.min(1, 0.35 + (n / max) * 0.65) : 0.18 }} />
      ))}
    </div>
  );
}

function Trace({ a, ink, pid, go }: { a: Area; ink: { ink: string; edge: string }; pid: string; go: (id: string) => void }) {
  const chip = "rounded bg-[#1c1d21] px-[7px] py-[3px] font-mono text-[11px] hover:bg-raised";
  const steps: { label: string; body: React.ReactNode }[] = [
    { label: "Requests come in", body: <div className="flex flex-wrap gap-1.5">{a.endpoints.map((e, i) => <button key={e} type="button" className={chip} onClick={() => a.node_ids.endpoints[i] && go(a.node_ids.endpoints[i])}>{e}</button>)}</div> },
    { label: "Code that handles them", body: <>
      <div className="flex flex-wrap gap-1.5">{a.handlers.map((h, i) => <button key={h + i} type="button" className={chip} onClick={() => go(a.node_ids.handlers[i])}>{h}</button>)}</div>
      {a.functions > a.handlers.length && <span className="text-xs text-muted">{a.functions} functions in total, across {a.files} file{a.files === 1 ? "" : "s"}</span>}
    </> },
    { label: "Data it reads and writes", body: a.tables.length
      ? <div className="flex flex-wrap gap-1.5">{a.tables.map((t, i) => <button key={t} type="button" className={chip} onClick={() => go(a.node_ids.tables[i])}>{t}{a.writes.includes(t) && <span className="ml-1 text-muted">writes</span>}</button>)}</div>
      : <span className="text-xs text-muted">No tables</span> },
    { label: "People who know it", body: a.people.length ? (
      <div className="flex flex-col gap-2">
        {a.people.map((p) => (
          <div key={p.login} className="flex items-center gap-2">
            <Initials name={p.name} size={24} />
            <span className="text-[13px]">{p.name}<span className="text-muted">, {p.prs} of {a.prs} PR{a.prs === 1 ? "" : "s"} here{p.member ? "" : " (not on this project)"}</span></span>
          </div>
        ))}
        {a.reviewers.length > 0 && <span className="text-xs text-muted">Reviewed by {a.reviewers.join(", ")}</span>}
      </div>) : <span className="text-xs text-muted">No pull request history for this code yet</span> },
  ];
  const solo = a.single_owner && a.people[0];
  return (
    <aside className="flex flex-col gap-[18px] overflow-hidden rounded-2xl border border-line bg-[#131417] px-6 py-[22px]">
      <div className="flex flex-col gap-1">
        <span className="text-xs text-muted">End to end{a.track ? <>, owned by <Link href={`/p/${pid}/tracks/${a.track.id}`} className="text-text-2 hover:underline">{a.track.name}</Link></> : ""}</span>
        <h2 className="text-[22px] font-semibold tracking-tight" style={{ color: ink.ink }}>{a.name}</h2>
      </div>
      <ol className="grid grid-cols-[18px_minmax(0,1fr)] gap-x-3.5">
        {steps.map((s, i) => (
          <li key={s.label} className="contents">
            <div className="flex flex-col items-center pt-[5px]">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: ink.ink }} />
              {i < steps.length - 1 && <span className="w-0.5 flex-1" style={{ background: ink.edge }} />}
            </div>
            <div className={`flex flex-col gap-1.5 ${i < steps.length - 1 ? "pb-4" : ""}`}>
              <span className="text-xs text-muted">{s.label}</span>
              {s.body}
            </div>
          </li>
        ))}
      </ol>
      <div className="flex-1" />
      {solo && (
        <div className="flex flex-col gap-2 rounded-[10px] border border-[#4a3a1e] bg-[#1a1712] px-3.5 py-3">
          <span className="text-[13px] leading-relaxed">Only {solo.name} has changed this area. If they&apos;re away, agents can explain it but nobody who knows it can approve a fix.</span>
          <Link href={`/p/${pid}?view=setup#people`} className="inline-flex h-9 items-center self-start rounded-md bg-accent px-3 text-xs font-semibold text-accent-ink">Add a second owner</Link>
        </div>
      )}
    </aside>
  );
}

function Explorer({ view, go, back }: { view: NodeView; go: (id: string) => void; back: () => void }) {
  const n = view.node;
  const incoming = view.groups.filter((g) => g.dir === "in");
  const outgoing = view.groups.filter((g) => g.dir === "out");
  return (
    <div className="flex flex-col gap-4">
      <button type="button" onClick={back} className="flex items-center gap-2 self-start text-sm text-muted hover:text-text"><ArrowLeft size={14} />Back to the map</button>
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)_minmax(0,1fr)]">
        <Side title="Points to this" groups={incoming} go={go} arrow="in" />
        <div className="enter flex flex-col gap-3 rounded-xl border border-text-2 bg-raised p-5 lg:sticky lg:top-20">
          <span className="font-mono text-[11px] text-muted">{n.type}</span>
          <h2 className="break-words font-mono text-lg">{n.label}</h2>
          {Object.entries(n.props).filter(([, v]) => v !== null && v !== "" && typeof v !== "object").slice(0, 6).map(([k, v]) => (
            <p key={k} className="flex justify-between gap-3 text-xs"><span className="text-muted">{k}</span><span className="truncate font-mono text-text-2">{String(v)}</span></p>
          ))}
          {n.text && <p className="max-h-48 overflow-auto whitespace-pre-wrap border-t border-line pt-3 text-[12px] leading-relaxed text-text-2">{n.text}</p>}
          <p className="text-[11px] text-muted">{view.groups.reduce((s, g) => s + g.count, 0)} connections</p>
        </div>
        <Side title="This points to" groups={outgoing} go={go} arrow="out" />
      </div>
    </div>
  );
}

function Side({ title, groups, go, arrow }: { title: string; groups: NodeView["groups"]; go: (id: string) => void; arrow: "in" | "out" }) {
  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-xs font-semibold text-muted">{title}</h3>
      {groups.length === 0 && <p className="text-xs text-faint">Nothing</p>}
      {groups.map((g) => (
        <section key={g.relation + g.type} className="flex flex-col gap-1.5 rounded-xl border border-line p-3">
          <p className="flex items-center gap-1.5 text-[11px] text-muted">
            {arrow === "in" ? <>{g.type}s <ArrowRight size={11} /> {g.relation}</> : <>{g.relation} <ArrowRight size={11} /> {g.type}s</>}
            <span className="ml-auto font-mono">{g.count}</span>
          </p>
          <div className="flex flex-wrap gap-1.5">
            {g.items.map((it) => (
              <button key={it.id} type="button" onClick={() => go(it.id)} className="max-w-full truncate rounded bg-raised px-2 py-1 font-mono text-[11px] text-text-2 hover:bg-line-strong hover:text-text">{it.label}</button>
            ))}
            {g.count > g.items.length && <span className="px-1 text-[11px] text-muted">+{g.count - g.items.length}</span>}
          </div>
        </section>
      ))}
    </div>
  );
}
