"use client";

import Link from "next/link";
import { Approval, ClusterRow, STATUS, usePoll } from "@/lib/api";

type Brief = { needs_you: Approval[]; clusters: ClusterRow[]; health: { name: string; note: string }[]; time: string };

export default function CtoBrief() {
  const { data: b } = usePoll<Brief>("/brief", 2000);
  if (!b) return <div className="p-8 text-sm text-muted">Loading brief</div>;
  const open = [...b.clusters].sort((x, y) => y.count - x.count);

  return (
    <div className="mx-auto flex min-h-[100dvh] max-w-[430px] flex-col gap-8 px-5 py-8">
      <header className="flex flex-col gap-1">
        <div className="flex items-baseline justify-between">
          <h1 className="text-xl font-semibold">CTO brief</h1>
          <span className="font-mono text-xs text-muted">{b.time}</span>
        </div>
        <p className="text-sm text-muted">Updates itself when something material changes.</p>
      </header>

      <section className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">Needs you</h2>
          <span className="font-mono text-sm text-accent">{b.needs_you.length}</span>
        </div>
        {b.needs_you.length === 0 && <p className="text-sm text-muted">Nothing needs your sign-off right now.</p>}
        {b.needs_you.map((a) => (
          <div key={a.id} className="flex flex-col gap-1 border-t border-line pt-3 first:border-0 first:pt-0">
            <p className="text-sm">{a.title}</p>
            {a.detail && <p className="text-xs text-muted">{a.detail}</p>}
          </div>
        ))}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold">Clusters</h2>
        {open.length === 0 && <p className="text-sm text-muted">No clusters yet.</p>}
        {open.map((c) => {
          const st = STATUS[c.status] ?? STATUS.new;
          return (
            <div key={c.id} className="flex flex-col gap-0.5">
              <div className="flex justify-between gap-3 text-sm">
                <span className="truncate"><span className="font-mono text-muted">{c.id}</span> {c.title}</span>
                <span className={`shrink-0 text-xs ${st.tone === "ok" ? "text-ok" : st.tone === "accent" ? "text-accent" : "text-muted"}`}>{st.label}</span>
              </div>
              <p className="text-xs text-muted">{c.owner ? `${c.owner}.` : "No owner yet."}{c.client_affected ? " Client affected." : ""} {c.count} tickets.</p>
            </div>
          );
        })}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold">Team health</h2>
        {b.health.length === 0 && <p className="text-sm text-muted">Everyone is inside the hours cap.</p>}
        {b.health.map((h) => (
          <div key={h.name} className="flex justify-between gap-3 text-sm">
            <span>{h.name}</span><span className="text-right text-xs text-muted">{h.note}</span>
          </div>
        ))}
      </section>

      <footer className="mt-auto flex items-center justify-between border-t border-line pt-4 text-xs text-muted">
        <span>Every line comes from the board. Nobody was pinged to write this.</span>
        <Link href="/warroom" className="hover:text-text">Board</Link>
      </footer>
    </div>
  );
}
