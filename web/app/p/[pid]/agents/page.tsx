"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { usePoll } from "@/lib/api";
import { Shell } from "@/components/shell";

type Tool = { name: string; risk: string; description: string; scope: string; calls: number };
type A = { id: string; name: string; trust: string; role: string; budget: string; prompt: string; tools: Tool[];
  stats: { runs: number; calls: number; blocked: number; errors: number; model_runs: number } };

const W = 1180, NW = 150, NH = 66;
const colX = (i: number, n: number) => 20 + i * ((W - 40 - NW) / Math.max(n - 1, 1));
const ROW = { people: 52, agents: 214 };

type N = { id: string; lane: "people" | "agents"; col: number; title: string; job: string; agent?: string; dashed?: boolean; gate?: boolean };
type E = { from: string; to: string; label?: string; dashed?: boolean };
type Flow = { id: string; name: string; says: string; cols: string[]; nodes: N[]; edges: E[] };

// Three flows. Agents do the legwork, people make the calls; a dashed step only happens when needed.
const FLOWS: Flow[] = [
  {
    id: "release", name: "Before a release", cols: ["Know the code", "Know the product", "See it run", "Predict risk", "Test", "Decide"],
    says: "Context comes from the repo, from what people say about the product, and from running the app on staging. Then risks, tests and a go/no-go a person owns.",
    nodes: [
      { id: "a-sync", lane: "agents", col: 0, title: "Context sync", job: "code, schema, PRs, people" },
      { id: "h-brief", lane: "people", col: 1, title: "The team", job: "answers the brief" },
      { id: "a-exp", lane: "agents", col: 2, title: "Staging explorer", job: "calls each request" },
      { id: "a-pm", lane: "agents", col: 3, title: "Pre-mortem", job: "risks from the diff" },
      { id: "a-test", lane: "agents", col: 4, title: "Agent tester", job: "safe checks", agent: "tester" },
      { id: "h-test", lane: "people", col: 4, title: "Human testers", job: "UI, devices, judgement" },
      { id: "h-go", lane: "people", col: 5, title: "Lead", job: "go or no-go, sets the watch" },
    ],
    edges: [
      { from: "a-sync", to: "a-exp", label: "endpoints" }, { from: "h-brief", to: "a-pm", label: "what matters" },
      { from: "a-exp", to: "a-pm", label: "what really runs" }, { from: "a-pm", to: "a-test", label: "checks" },
      { from: "a-test", to: "h-test", label: "needs a person", dashed: true }, { from: "a-test", to: "h-go", label: "results" },
      { from: "h-test", to: "h-go", label: "results" },
    ],
  },
  {
    id: "bug", name: "A bug", cols: ["Find", "Understand", "Reproduce", "Assign", "Review", "Fix", "Verify"],
    says: "Agents find the cause, reproduce it and propose who fixes it. If the owner isn't senior in that area, the expert reviews the plan; risky changes wait for a senior.",
    nodes: [
      { id: "h-find", lane: "people", col: 0, title: "Testers", job: "report bugs" },
      { id: "a-find", lane: "agents", col: 0, title: "Agent tester", job: "files what it finds", agent: "tester" },
      { id: "a-rc", lane: "agents", col: 1, title: "Root cause", job: "Jev: how sure, per change", agent: "root-cause" },
      { id: "a-rp", lane: "agents", col: 2, title: "Repro agent", job: "steps, runs them", agent: "repro" },
      { id: "h-rp", lane: "people", col: 2, title: "Tester records it", job: "when the agent can't", dashed: true },
      { id: "a-dp", lane: "agents", col: 3, title: "Dispatcher", job: "owner + ETA", agent: "dispatcher" },
      { id: "a-ex", lane: "agents", col: 4, title: "Expert", job: "area + team practices", agent: "expert" },
      { id: "h-sign", lane: "people", col: 4, title: "Senior", job: "signs off risky fixes", dashed: true },
      { id: "h-fix", lane: "people", col: 5, title: "Owner", job: "agrees ETA, fixes" },
      { id: "h-ver", lane: "people", col: 6, title: "Tester", job: "verifies on staging" },
    ],
    edges: [
      { from: "a-find", to: "a-rc", label: "bug" }, { from: "h-find", to: "a-rc", label: "bug" }, { from: "a-rc", to: "a-rp", label: "cause" },
      { from: "a-rp", to: "h-rp", label: "asks for help", dashed: true }, { from: "a-rp", to: "a-dp", label: "repro" },
      { from: "a-dp", to: "a-ex", label: "if not senior", dashed: true }, { from: "a-ex", to: "h-sign", label: "risky", dashed: true },
      { from: "a-dp", to: "h-fix", label: "proposes" }, { from: "h-fix", to: "h-ver", label: "fixed" },
    ],
  },
  {
    id: "prod", name: "In production", cols: ["Watch", "Judge", "Open", "Diagnose", "Own", "Mitigate", "Learn"],
    says: "The monitoring agent watches queries and errors, tighter during a release's watch window. Jev judges real or noise; people open war rooms, approve every mitigation and resolve.",
    nodes: [
      { id: "a-mon", lane: "agents", col: 0, title: "Monitoring agent", job: "queries, errors, release watch" },
      { id: "a-jev", lane: "agents", col: 1, title: "Jev", job: "real incident or noise?", gate: true },
      { id: "h-open", lane: "people", col: 2, title: "On-call", job: "opens or dismisses" },
      { id: "a-diag", lane: "agents", col: 3, title: "Diagnosis", job: "Jev ranks the likely change" },
      { id: "a-own", lane: "agents", col: 4, title: "Dispatcher", job: "who knows that code" },
      { id: "h-own", lane: "people", col: 4, title: "Owner", job: "takes it" },
      { id: "h-mit", lane: "people", col: 5, title: "Owner or lead", job: "approves each fix step" },
      { id: "a-learn", lane: "agents", col: 6, title: "Experts", job: "learn what fixed it" },
    ],
    edges: [
      { from: "a-mon", to: "a-jev", label: "signal" }, { from: "a-jev", to: "h-open", label: "likely real" },
      { from: "h-open", to: "a-diag", label: "war room" }, { from: "a-diag", to: "a-own", label: "cause" },
      { from: "a-own", to: "h-own", label: "proposes" }, { from: "h-own", to: "h-mit", label: "plan" }, { from: "h-mit", to: "a-learn", label: "resolved" },
    ],
  },
];

const RISK: Record<string, { dot: string; label: string }> = {
  read: { dot: "bg-text-2", label: "reads" }, ask: { dot: "bg-ok", label: "asks a person" }, write: { dot: "bg-text", label: "proposes a change" },
};

export default function Agents() {
  const { pid } = useParams<{ pid: string }>();
  const { data: p } = usePoll<{ project: { name: string } }>(`/projects/${pid}`, 60000);
  const { data: agents } = usePoll<A[]>(`/agents?project=${pid}`, 3000);
  const [sel, setSel] = useState("root-cause");
  const [fid, setFid] = useState("bug");
  const byId = Object.fromEntries((agents ?? []).map((a) => [a.id, a]));
  const f = FLOWS.find((x) => x.id === fid)!;
  const pos = (n: N) => ({ x: colX(n.col, f.cols.length), y: ROW[n.lane] });
  const a = byId[sel];

  return (
    <Shell wide crumbs={[{ label: p?.project.name ?? "Project", href: `/p/${pid}` }, { label: "How work flows" }]}>
      <div className="flex flex-col gap-6">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">How work flows</h1>
            <p className="max-w-[80ch] text-sm text-[#b8b7b2]">{f.says}</p>
          </div>
          <nav className="flex gap-1 rounded-[10px] border border-line bg-panel p-1" aria-label="Flow">
            {FLOWS.map((x) => (
              <button key={x.id} type="button" onClick={() => setFid(x.id)} aria-current={fid === x.id ? "page" : undefined}
                className={`h-9 rounded-[7px] px-4 text-[13px] ${fid === x.id ? "bg-[#2b2d33] font-medium text-text" : "text-muted hover:text-text"}`}>{x.name}</button>
            ))}
          </nav>
        </header>

        <div className="overflow-x-auto rounded-xl border border-line">
          <svg width={W} height={320} viewBox={`0 0 ${W} 320`} role="img" aria-label={`${f.name}: flow of work between people and agents`}>
            <rect x="0" y="30" width={W} height="110" fill="var(--panel)" opacity="0.6" />
            <text x="12" y="46" fontSize="11" fill="var(--text-2)">People</text>
            <text x="12" y="208" fontSize="11" fill="var(--muted)">Agents</text>
            {f.cols.map((c, i) => <text key={c} x={colX(i, f.cols.length) + NW / 2} y="18" textAnchor="middle" fontSize="11" fill="var(--muted)">{c}</text>)}
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" fill="var(--line-strong)" />
              </marker>
            </defs>
            {f.edges.map((e, i) => {
              const s1 = f.nodes.find((n) => n.id === e.from)!, t = f.nodes.find((n) => n.id === e.to)!;
              const a1 = pos(s1), b1 = pos(t);
              const vertical = s1.col === t.col;
              const x1 = vertical ? a1.x + NW / 2 : a1.x + NW, y1 = vertical ? (a1.y < b1.y ? a1.y + NH : a1.y) : a1.y + NH / 2;
              const x2 = vertical ? b1.x + NW / 2 : b1.x, y2 = vertical ? (a1.y < b1.y ? b1.y : b1.y + NH) : b1.y + NH / 2;
              const mx = (x1 + x2) / 2;
              const d = vertical ? `M${x1},${y1} L${x2},${y2 + (y2 > y1 ? -2 : 2)}` : `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2 - 2},${y2}`;
              return (
                <g key={i}>
                  <path d={d} fill="none" stroke="var(--line-strong)" strokeWidth="1.5" strokeDasharray={e.dashed ? "5 4" : undefined} markerEnd="url(#arrow)" />
                  {e.label && <text x={vertical ? x1 + 8 : mx} y={vertical ? (y1 + y2) / 2 + 4 : (y1 + y2) / 2 - 6}
                    textAnchor={vertical ? "start" : "middle"} fontSize="10" fill="var(--muted)">{e.label}</text>}
                </g>
              );
            })}
            {f.nodes.map((n) => {
              const { x, y } = pos(n);
              const ag = n.agent ? byId[n.agent] : null;
              const active = n.agent === sel;
              const agent = n.lane === "agents";
              return (
                <g key={n.id} onClick={() => n.agent && setSel(n.agent)} style={{ cursor: n.agent ? "pointer" : "default" }}>
                  <rect x={x} y={y} width={NW} height={NH} rx={n.gate ? 4 : agent ? 10 : NH / 2}
                    fill={active ? "var(--raised)" : "var(--bg)"} strokeDasharray={n.dashed ? "5 4" : undefined}
                    stroke={active ? "var(--text)" : agent ? "var(--line-strong)" : "var(--text-2)"} strokeWidth={active ? 2 : 1} />
                  <text x={x + NW / 2} y={y + 25} textAnchor="middle" fontSize="13" fontWeight="500" fill="var(--text)">{n.title}</text>
                  <text x={x + NW / 2} y={y + 42} textAnchor="middle" fontSize="10.5" fill="var(--muted)">{n.job}</text>
                  {ag && <text x={x + NW / 2} y={y + 57} textAnchor="middle" fontSize="10" fill="var(--text-2)" fontFamily="var(--font-geist-mono)">
                    {ag.stats.calls} tool calls{ag.stats.blocked ? `, ${ag.stats.blocked} blocked` : ""}</text>}
                </g>
              );
            })}
          </svg>
        </div>
        <p className="-mt-3 text-xs text-muted">Round boxes are people, square ones agents. Dashed steps only happen when needed. Click an agent with a tool count to see what it may do.</p>

        {a && (
          <section className="enter grid gap-6 rounded-xl border border-line p-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
            <div className="flex flex-col gap-3">
              <h2 className="text-lg font-semibold">{a.name}</h2>
              <p className="text-sm leading-relaxed text-text-2">{a.prompt.split(". ")[0]}.</p>
              <div className="grid grid-cols-3 gap-3 pt-2">
                <Stat n={a.stats.runs} label="runs" />
                <Stat n={a.stats.calls} label="tool calls" />
                <Stat n={a.stats.blocked} label="blocked by hooks" />
              </div>
              <p className="text-xs text-muted">
                {a.role === "none" ? "Always follows its fixed plan (rules, not a model)." : a.stats.model_runs ? `${a.stats.model_runs} runs driven by the model.` : "Follows its scripted plan until a model key is set; then the model picks the tools."}
                {" "}Trust: {a.trust}. Up to {a.budget} steps a run. Defined in agents/{a.id}.md.
              </p>
            </div>
            <div className="flex flex-col gap-2">
              <h3 className="text-xs font-semibold text-muted">Tools, and where each one really works</h3>
              <ul className="flex flex-col gap-2">
                {a.tools.map((t) => {
                  const max = Math.max(1, ...a.tools.map((x) => x.calls));
                  return (
                    <li key={t.name} className="grid grid-cols-[minmax(0,1fr)_120px] items-center gap-4 rounded-lg bg-panel px-3 py-2.5">
                      <div className="min-w-0">
                        <p className="flex flex-wrap items-center gap-2 text-sm">
                          <span className={`h-2 w-2 rounded-full ${RISK[t.risk].dot}`} title={RISK[t.risk].label} />
                          <span className="font-mono">{t.name}</span>
                          <span className={`rounded px-1.5 py-0.5 text-[10px] ${t.scope === "any repo" ? "bg-ok-soft text-ok" : t.scope === "model only" ? "bg-raised text-muted" : "bg-raised text-text-2"}`}>{t.scope}</span>
                        </p>
                        <p className="mt-0.5 truncate text-[11px] text-muted" title={t.description}>{t.description}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 flex-1 rounded-full bg-raised"><div className="h-1.5 rounded-full bg-text-2" style={{ width: `${(t.calls / max) * 100}%` }} /></div>
                        <span className="w-8 text-right font-mono text-xs text-text-2">{t.calls}</span>
                      </div>
                    </li>
                  );
                })}
              </ul>
              <p className="flex gap-4 pt-1 text-[11px] text-muted">
                {Object.entries(RISK).map(([k, v]) => <span key={k} className="flex items-center gap-1.5"><span className={`h-2 w-2 rounded-full ${v.dot}`} />{v.label}</span>)}
              </p>
            </div>
          </section>
        )}
      </div>
    </Shell>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return <div><p className="font-mono text-xl">{n}</p><p className="text-[11px] text-muted">{label}</p></div>;
}
