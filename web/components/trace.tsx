"use client";

import { CheckCircle, Prohibit, Robot, WarningCircle } from "@phosphor-icons/react";

export type Run = { id: string; agent: string; trust: string; tools: string[]; policy: string; status: string; ms: number;
  steps: { tool: string; risk?: string; summary: string; ms: number; ok: boolean; blocked?: boolean; cites?: string[] }[] };

const RISK: Record<string, string> = { read: "text-muted", ask: "text-ok", write: "text-accent" };

/** What each agent actually did: every tool call through the harness, in order, with what it found. */
export function AgentTrace({ runs }: { runs: Run[] }) {
  if (!runs.length) return null;
  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold">What the agents did</h2>
        <span className="text-[11px] text-muted">Every call goes through the harness: role-file allowlist, trust check, citation gate, logged.</span>
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        {runs.map((r) => (
          <article key={r.id} className="flex flex-col gap-3 rounded-xl border border-line p-4">
            <header className="flex items-center gap-2">
              <span className="flex h-7 w-7 items-center justify-center rounded-full bg-agent-soft text-agent"><Robot size={15} /></span>
              <span className="flex-1 text-sm font-medium">{r.agent}</span>
              <span className="font-mono text-[10px] text-muted" title={r.policy === "scripted" ? "No model key: a scripted plan drove the same tools" : "The model chose each tool"}>
                {r.policy}, {r.ms}ms
              </span>
            </header>
            <ol className="flex flex-col gap-2">
              {r.steps.map((s, i) => (
                <li key={i} className="grid grid-cols-[16px_minmax(0,1fr)] gap-2 text-xs">
                  <span className="mt-0.5">
                    {s.blocked ? <Prohibit size={14} className="text-bad" /> : s.ok ? <CheckCircle size={14} className="text-ok" /> : <WarningCircle size={14} className="text-bad" />}
                  </span>
                  <span className="min-w-0">
                    <span className={`font-mono ${s.risk ? RISK[s.risk] : "text-muted"}`}>{s.tool}</span>
                    <span className="ml-1 text-faint">{s.ms ? `${s.ms}ms` : ""}</span>
                    <span className={`block break-words ${s.blocked ? "text-bad" : "text-text-2"}`}>{s.summary}</span>
                  </span>
                </li>
              ))}
            </ol>
            <footer className="mt-auto flex flex-wrap gap-1 border-t border-line pt-2">
              {r.tools.map((t) => <span key={t} className="rounded bg-raised px-1.5 py-0.5 font-mono text-[10px] text-muted">{t}</span>)}
              <span className="ml-auto text-[10px] text-muted">trust: {r.trust}</span>
            </footer>
          </article>
        ))}
      </div>
    </section>
  );
}
