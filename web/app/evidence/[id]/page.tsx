"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, CheckCircle } from "@phosphor-icons/react";
import { ClusterDetail, usePoll } from "@/lib/api";

export default function Evidence() {
  const { id } = useParams<{ id: string }>();
  const { data: c } = usePoll<ClusterDetail>(`/clusters/${id}`, 2000);

  if (!c) return <div className="p-10 text-sm text-muted">Loading evidence</div>;
  const top = c.theories.find((t) => t.status === "confirmed") ?? c.theories.find((t) => t.status === "open");
  const ruled = c.theories.filter((t) => t.status === "ruled_out" || t.status === "refuted");
  const searches = c.evidence.length;

  return (
    <div className="min-h-[100dvh]">
      <header className="flex h-14 items-center gap-4 border-b border-line px-5">
        <Link href="/warroom" className="inline-flex items-center gap-2 text-sm text-muted hover:text-text"><ArrowLeft size={14} /> Board</Link>
        <span className="font-mono text-sm">{c.id}</span>
        <span className="text-sm text-muted">Evidence chain for the top theory</span>
        <div className="flex-1" />
        <span className="font-mono text-xs text-muted">{searches} steps, built in {c.evidence_ms}ms</span>
      </header>

      <div className="mx-auto grid max-w-[1180px] gap-10 px-6 py-10 md:grid-cols-[minmax(0,1fr)_340px]">
        <main className="flex flex-col gap-8">
          <div className="flex flex-col gap-3">
            <h1 className="max-w-[40ch] text-2xl font-semibold leading-snug tracking-tight">{top?.text ?? c.title}</h1>
            <p className="text-sm text-muted">Every claim below cites a card. Anything without a source was rejected before you saw it.</p>
          </div>
          <ol className="flex flex-col">
            {c.evidence.map((s, i) => (
              <li key={s.name} className="grid grid-cols-[24px_minmax(0,1fr)] gap-4">
                <div className="flex flex-col items-center">
                  <span className={`mt-1.5 h-2.5 w-2.5 rounded-full ${i === c.evidence.length - 1 ? "bg-accent" : "bg-faint"}`} />
                  {i < c.evidence.length - 1 && <span className="w-px flex-1 bg-line" />}
                </div>
                <div className="flex flex-col gap-2 pb-7">
                  <div className="flex items-baseline justify-between gap-4">
                    <span className="text-sm font-semibold">{s.name}</span>
                    <span className="text-xs text-muted">{s.source}</span>
                  </div>
                  <p className="text-sm leading-relaxed text-text-2">{s.summary}</p>
                  {s.name === "Where in the code" && s.cites.map((cite) => (
                    <pre key={cite} className="overflow-x-auto rounded-lg border border-line bg-panel p-3 font-mono text-[12px] leading-relaxed text-text-2">{c.cards[cite]}</pre>
                  ))}
                  {s.name !== "Where in the code" && s.cites.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {s.cites.map((cite) => (
                        <span key={cite} title={c.cards[cite]} className="rounded-md border border-line-strong px-2 py-0.5 font-mono text-[11px] text-muted">{cite}</span>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </main>

        <aside className="flex flex-col gap-6">
          {top?.status === "confirmed" ? (
            <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-5">
              <div className="flex items-center gap-2 text-ok"><CheckCircle size={18} /><h2 className="text-sm font-semibold">Confirmed by the data</h2></div>
              <p className="text-sm text-text-2">{top.check_result}</p>
            </section>
          ) : (
            <section className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-5">
              <h2 className="text-sm font-semibold">Not confirmed yet</h2>
              <p className="text-sm text-muted">Run the check from the board to confirm or refute this theory.</p>
            </section>
          )}
          {ruled.length > 0 && (
            <section className="flex flex-col gap-2">
              <h2 className="text-sm font-semibold">Ruled out</h2>
              {ruled.map((t) => <p key={t.id} className="text-sm text-muted"><span className="line-through">{t.text}</span>{t.ruled_out_by ? `, ${t.ruled_out_by}` : ""}</p>)}
            </section>
          )}
          <p className="text-xs text-muted">Agents propose and run read-only checks after approval. Nothing here writes to production.</p>
        </aside>
      </div>
    </div>
  );
}
