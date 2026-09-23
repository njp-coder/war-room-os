"use client";

import { useEffect, useRef } from "react";

type Node = { id: string; label: string; col: number; hot: boolean; kind: string; step?: string };
export type DiagramData = { columns: string[]; col_step?: string[]; nodes: Node[]; edges: { from: string; to: string }[] };

const COL_W = 184;
const NODE_W = 156;
const NODE_H = 40;
const GAP = 12;
const TOP = 30;

/**
 * Path from what broke to the people who know the code. Nodes light up the moment the agent step
 * that confirms them lands, so the cause is traced live, left to right. Bright path is the best guess.
 * `reached` is the set of relay steps done; omit it and everything counts as confirmed.
 */
export function FlowDiagram({ data, reached, working }: { data: DiagramData; reached?: Set<string>; working?: boolean }) {
  const seen = useRef<Set<string> | null>(null);
  const confirmed = (step?: string) => !reached || !step || reached.has(step);
  const colStep = data.col_step ?? [];
  // Hide empty columns once their step is done; keep the next unconfirmed one as a placeholder.
  const nextOpen = colStep.findIndex((s) => !confirmed(s));
  const visible = data.columns.map((_, i) => i).filter((i) => data.nodes.some((n) => n.col === i) || (working && !confirmed(colStep[i]) && colStep[i] === colStep[nextOpen]));
  const slot = Object.fromEntries(visible.map((c, i) => [c, i]));
  const cols = visible.map((c) => data.nodes.filter((n) => n.col === c && confirmed(n.step)));
  const rows = Math.max(1, ...cols.map((c) => c.length));
  const height = TOP + rows * (NODE_H + GAP);
  const width = visible.length * COL_W;
  const pos: Record<string, { x: number; y: number; col: number }> = {};
  cols.forEach((col, ci) => {
    col.forEach((n, ri) => { pos[n.id] = { x: ci * COL_W + (COL_W - NODE_W) / 2, y: TOP + ri * (NODE_H + GAP), col: ci }; });
  });
  const hot = new Set(data.nodes.filter((n) => n.hot).map((n) => n.id));

  // First paint replays the trace; after that only newly confirmed nodes light up.
  const fresh = new Set<string>();
  const first = seen.current === null;
  for (const id of Object.keys(pos)) if (first || !seen.current!.has(id)) fresh.add(id);
  useEffect(() => { seen.current = new Set(Object.keys(pos)); });
  const delay = (col: number) => `${first ? col * 260 : 0}ms`;

  return (
    <div className="overflow-x-auto">
      <div className="relative" style={{ width, height }} role="img" aria-label="Path from what broke to the code, data, change and people">
        {visible.map((c, i) => (
          <span key={c} className="absolute top-0 text-[11px] text-muted" style={{ left: i * COL_W + (COL_W - NODE_W) / 2 }}>{data.columns[c]}</span>
        ))}
        <svg className="pointer-events-none absolute inset-0" width={width} height={height} aria-hidden="true">
          {data.edges.map((e) => {
            const a = pos[e.from], b = pos[e.to];
            if (!a || !b) return null;
            const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2;
            const mid = (x1 + x2) / 2;
            const isHot = hot.has(e.from) && hot.has(e.to);
            return <path key={`${e.from}>${e.to}`} d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`} fill="none"
              className={fresh.has(e.to) ? "trace" : ""} style={{ animationDelay: delay(b.col) }}
              stroke={isHot ? "var(--text)" : "var(--line-strong)"} strokeOpacity={isHot ? 0.85 : 1} strokeWidth={isHot ? 1.75 : 1} />;
          })}
        </svg>
        {cols.flat().map((n) => {
          const p = pos[n.id];
          return (
            <div key={n.id} title={n.label}
              className={`absolute flex items-center justify-center px-3 text-center text-[12px] ${fresh.has(n.id) ? "lit arrive" : ""} ${n.kind === "person" ? "rounded-full" : "rounded-lg"}
                ${n.hot ? "border border-text-2 bg-raised text-text" : "border border-line bg-panel text-muted"} ${n.col === 2 || n.col === 3 ? "font-mono text-[11.5px]" : ""}`}
              style={{ left: p.x, top: p.y, width: NODE_W, height: NODE_H, animationDelay: delay(p.col) }}>
              <span className="truncate">{n.label}</span>
            </div>
          );
        })}
        {visible.map((c, i) => !cols[i].length && (
          <div key={`wait-${c}`} className="absolute flex items-center justify-center rounded-lg border border-dashed border-line-strong text-[11px] text-muted"
            style={{ left: i * COL_W + (COL_W - NODE_W) / 2, top: TOP, width: NODE_W, height: NODE_H }}>
            <span className="animate-pulse">agent looking</span>
          </div>
        ))}
      </div>
    </div>
  );
}
