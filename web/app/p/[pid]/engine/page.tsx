"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { get, post, usePoll } from "@/lib/api";
import { Field, Section, Shell, inputCls } from "@/components/shell";
import { Button } from "@/components/ui";

type Out = { sections: { title: string; items: { id: string; text: string; why: string }[] }[]; tokens: number; budget: number; dropped: number; ms: number };
type Eval = { tasks: number; k: number; results: Record<string, { recall: number; precision: number; tokens: number }>; note: string };

const OPS = [
  { id: "understand", label: "Understand", hint: "Current flow, where it lives, data, recent changes, who knows it" },
  { id: "precedent", label: "Find precedent", hint: "Where we've done something like this, with tests and wiring to copy" },
  { id: "impact", label: "Impact", hint: "What changing a file, symbol or field touches" },
  { id: "conventions", label: "Conventions", hint: "Where each kind of code lives in this repo" },
  { id: "context", label: "Context for an agent", hint: "Stateful: add what the agent edited, get what became relevant" },
];

export default function EnginePage() {
  const { pid } = useParams<{ pid: string }>();
  const { data: p } = usePoll<{ project: { name: string } }>(`/projects/${pid}`, 60000);
  const [op, setOp] = useState("understand");
  const [task, setTask] = useState("");
  const [edited, setEdited] = useState("");
  const [budget, setBudget] = useState(1500);
  const [out, setOut] = useState<Out | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ev, setEv] = useState<Eval | null>(null);
  const [evBusy, setEvBusy] = useState(false);

  const run = async () => {
    if (op !== "conventions" && !task.trim()) return setErr(op === "impact" ? "Name a file, symbol or field." : "Describe the task.");
    setBusy(true); setErr(null);
    try {
      setOut(await post(`/projects/${pid}/engine`, {
        op, task, target: task, budget,
        state: op === "context" && edited.trim() ? { edited: edited.split(",").map((s) => s.trim()).filter(Boolean) } : {},
      }));
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };

  return (
    <Shell crumbs={[{ label: p?.project.name ?? "Project", href: `/p/${pid}` }, { label: "Context engine" }]}>
      <div className="flex flex-col gap-10">
        <header className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">Context engine</h1>
          <p className="max-w-[70ch] text-sm text-muted">The smallest context that is sufficient for the right decision. Every item says why it&apos;s there. Built from the graph and Moss search, with zero model tokens.</p>
        </header>

        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          <div className="flex flex-col gap-5">
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Operation">
              {OPS.map((o) => (
                <button key={o.id} type="button" role="radio" aria-checked={op === o.id} onClick={() => setOp(o.id)}
                  className={`h-9 rounded-lg border px-3 text-sm ${op === o.id ? "border-accent text-accent" : "border-line-strong text-text-2 hover:bg-raised"}`}>{o.label}</button>
              ))}
            </div>
            <p className="text-xs text-muted">{OPS.find((o) => o.id === op)?.hint}</p>
            {op !== "conventions" && (
              <Field label={op === "impact" ? "File, symbol or field" : "Task"}>
                <input className={inputCls} value={task} onChange={(e) => setTask(e.target.value)} onKeyDown={(e) => e.key === "Enter" && run()}
                  placeholder={op === "impact" ? "backend/app/crud.py or users.email" : "Add rate limiting to login"} />
              </Field>
            )}
            {op === "context" && (
              <Field label="Symbols the agent edited (optional)" hint="Comma separated node IDs, e.g. sym:owner/repo:path/file.py#function">
                <input className={inputCls} value={edited} onChange={(e) => setEdited(e.target.value)} />
              </Field>
            )}
            <Field label={`Token budget: ${budget}`}>
              <input type="range" min={300} max={6000} step={100} value={budget} onChange={(e) => setBudget(Number(e.target.value))} />
            </Field>
            {err && <p className="text-sm text-bad">{err}</p>}
            <Button variant="primary" className="self-start" disabled={busy} onClick={run}>{busy ? "Thinking" : "Get context"}</Button>
          </div>

          <div className="min-h-[240px] rounded-xl border border-line p-5">
            {!out && <p className="text-sm text-muted">Results appear here, packed to your budget.</p>}
            {out && (
              <div className="flex flex-col gap-5">
                <p className="font-mono text-xs text-muted">{out.tokens} of {out.budget} tokens, {out.ms}ms{out.dropped ? `, ${out.dropped} items left out` : ""}</p>
                {out.sections.length === 0 && <p className="text-sm text-muted">Nothing relevant found. Try naming a file or endpoint.</p>}
                {out.sections.map((s) => (
                  <section key={s.title} className="flex flex-col gap-2">
                    <h3 className="text-xs font-semibold text-muted">{s.title}</h3>
                    <ul className="flex flex-col gap-1.5">
                      {s.items.map((i) => (
                        <li key={i.id + i.text} className="text-sm"><span className="break-words">{i.text}</span> <span className="text-xs text-faint">{i.why}</span></li>
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </div>
        </div>

        <Section title="How good is the context? Recall@10 on this repo's own PRs"
          action={<Button size="sm" disabled={evBusy} onClick={async () => { setEvBusy(true); try { setEv(await get(`/projects/${pid}/engine/eval`)); } finally { setEvBusy(false); } }}>{evBusy ? "Measuring" : "Run evaluation"}</Button>}>
          <p className="max-w-[70ch] text-sm text-muted">Each merged PR becomes a task (its title). The files it actually changed are the answer. We measure how many of them each method surfaces before the agent explores anything.</p>
          {ev && (
            <div className="flex flex-col gap-3">
              <div className="grid grid-cols-4 gap-4 border-b border-line pb-2 text-xs text-muted"><span>Method</span><span>Recall@{ev.k}</span><span>Precision</span><span>Tokens</span></div>
              {Object.entries(ev.results).map(([m, r]) => (
                <div key={m} className={`grid grid-cols-4 gap-4 text-sm ${m === "engine" ? "text-accent" : ""}`}>
                  <span>{m === "engine" ? "Context engine" : m === "vector" ? "Vector search only" : "Keyword search"}</span>
                  <span className="font-mono">{Math.round(r.recall * 100)}%</span>
                  <span className="font-mono">{Math.round(r.precision * 100)}%</span>
                  <span className="font-mono">{r.tokens}</span>
                </div>
              ))}
              <p className="text-xs text-muted">{ev.tasks} tasks. {ev.note}</p>
            </div>
          )}
        </Section>

        <Section title="Use it from a coding agent">
          <p className="text-sm text-muted">The same engine runs as an MCP server, so Claude Code or Cursor can call understand, precedent, impact and context before they grep.</p>
          <pre className="overflow-x-auto rounded-lg border border-line bg-panel p-4 font-mono text-xs text-text-2">{`claude mcp add warroom -e WARROOM_PROJECT=${pid} -- \\
  bash -c "cd ~/Documents/war-room-os && pyenv/bin/python -m backend.mcp_server"`}</pre>
        </Section>
      </div>
    </Shell>
  );
}
