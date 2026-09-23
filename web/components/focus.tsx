"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowRight, CheckCircle, Users } from "@phosphor-icons/react";
import { Approval, ClusterDetail, ClusterRow, STATUS, post, usePoll } from "@/lib/api";
import { Badge, Button } from "./ui";

export function Focus({ id, approvals, clusters }: { id: string; approvals: Approval[]; clusters: ClusterRow[] }) {
  const { data: c, reload } = usePoll<ClusterDetail>(`/clusters/${id}`, 1000);
  const [busy, setBusy] = useState<string | null>(null);

  if (!c) return <FocusSkeleton />;

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    try { await fn(); await reload(); } finally { setBusy(null); }
  };

  const open = c.theories.filter((t) => t.status === "open");
  const top = c.theories.find((t) => t.status === "confirmed") ?? open[0];
  const alternates = open.filter((t) => t !== top);
  const ruled = c.theories.filter((t) => t.status === "ruled_out" || t.status === "refuted");
  const mine = approvals.filter((a) => a.cluster === c.id);
  const check = mine.find((a) => a.kind === "run_check");
  const assign = mine.find((a) => a.kind === "assign");
  const client = mine.find((a) => a.kind === "client_update");
  const st = STATUS[c.status] ?? STATUS.new;
  const twin = clusters.find((o) => o.id !== c.id && c.fields.length > 0 && o.top_theory == null && o.title !== c.title &&
    o.count < c.count && sameFields(o, c));

  return (
    <article className="enter mx-auto flex w-full max-w-[860px] flex-col gap-8">
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
          <span className="font-mono text-sm text-text">{c.id}</span>
          <Badge tone={st.tone}>{st.label}</Badge>
          <span><span className="font-mono text-text">{c.count}</span> tickets</span>
          <span>since {c.first_seen}</span>
          {c.client_affected && <Badge tone="bad">Client affected</Badge>}
        </div>
        <h1 className="text-2xl font-semibold leading-tight tracking-tight">{c.title}</h1>
        {c.step && <p className="text-sm text-muted">{c.step}</p>}
      </header>

      {!top && (
        <section className="flex flex-col items-start gap-4 rounded-xl border border-line bg-panel p-6">
          <p className="max-w-[60ch] text-sm text-text-2">
            {c.status === "investigating"
              ? "Root cause is building the evidence packet."
              : "Nobody is on this yet. Root cause builds the evidence from code, changes, logs and past incidents, then proposes a check."}
          </p>
          <Button variant="primary" disabled={busy !== null || c.status === "investigating"} onClick={() => act("inv", () => post(`/clusters/${c.id}/investigate`))}>
            Investigate <ArrowRight size={16} />
          </Button>
          <ul className="mt-2 flex flex-col gap-1 text-sm text-muted">
            {c.sample_tickets.slice(0, 3).map((t, i) => <li key={i}>&ldquo;{t}&rdquo;</li>)}
          </ul>
        </section>
      )}

      {top && (
        <section className="grid gap-8 md:grid-cols-[minmax(0,1fr)_260px]">
          <div className="flex flex-col gap-5">
            <div className="flex items-start gap-4">
              <span className={`w-16 shrink-0 font-mono text-3xl leading-none ${top.status === "confirmed" ? "text-ok" : "text-accent"}`}>
                {top.status === "confirmed" ? <CheckCircle size={30} weight="regular" /> : `${top.confidence}%`}
              </span>
              <p className="text-base leading-relaxed">{top.text}</p>
            </div>
            <div className="flex flex-wrap gap-2 pl-20">
              {top.cites.slice(0, 4).map((cite) => (
                <span key={cite} title={c.cards[cite]} className="rounded-md border border-line-strong px-2 py-1 font-mono text-[11px] text-text-2">{cite}</span>
              ))}
            </div>
            {top.check_result && (
              <p className={`pl-20 text-sm ${top.status === "confirmed" ? "text-ok" : "text-muted"}`}>{top.check_result}</p>
            )}
            {alternates.map((t) => (
              <div key={t.id} className="flex items-baseline gap-4 text-muted">
                <span className="w-16 shrink-0 font-mono text-sm">{t.confidence}%</span>
                <span className="flex-1 text-sm">{t.text}</span>
                <Button variant="quiet" size="sm" disabled={busy !== null} onClick={() => act(t.id, () => post(`/clusters/${c.id}/theories/${t.id}/rule_out`, { reason: "" }))}>Rule out</Button>
              </div>
            ))}
            {ruled.map((t) => (
              <div key={t.id} className="flex items-baseline gap-4 text-sm text-faint">
                <span className="w-16 shrink-0">{t.status === "refuted" ? "Refuted" : "Ruled out"}</span>
                <span><span className="line-through">{t.text}</span>{t.ruled_out_by ? `, ${t.ruled_out_by}` : ""}</span>
              </div>
            ))}
          </div>

          <aside className="flex flex-col gap-3 border-line md:border-l md:pl-6">
            {top.status === "open" && top.check_sql && (
              <>
                <span className="text-xs text-muted">The check that settles it</span>
                <pre className="whitespace-pre-wrap font-mono text-[11.5px] leading-relaxed text-text-2">{formatSql(top.check_sql)}</pre>
                <span className="text-xs text-muted">Read-only replica, 5s limit</span>
                <div className="flex gap-2">
                  {check ? (
                    <Button variant="primary" className="flex-1" disabled={busy !== null}
                      onClick={() => act("check", () => post(`/approvals/${check.id}`, { action: "approve" }))}>Run check</Button>
                  ) : (
                    <Button variant="ghost" className="flex-1" disabled>Queued</Button>
                  )}
                  <Link href={`/evidence/${c.id}`} className="inline-flex h-10 items-center rounded-lg border border-line-strong px-4 text-sm hover:bg-raised">Evidence</Link>
                </div>
                {top && (
                  <Button variant="quiet" size="sm" className="self-start px-0" disabled={busy !== null}
                    onClick={() => act(top.id, () => post(`/clusters/${c.id}/theories/${top.id}/rule_out`, { reason: "" }))}>Rule out this theory</Button>
                )}
              </>
            )}
            {top.status === "confirmed" && (
              <>
                <span className="text-xs text-muted">Confirmed by the data</span>
                <Link href={`/evidence/${c.id}`} className="inline-flex h-10 items-center justify-center rounded-lg border border-line-strong px-4 text-sm hover:bg-raised">See the evidence chain</Link>
              </>
            )}
          </aside>
        </section>
      )}

      {(assign || c.owner || client) && (
        <section className="flex flex-col gap-3 border-t border-line pt-6">
          {c.owner && (
            <p className="flex items-center gap-2 text-sm text-text-2"><Users size={16} className="text-muted" />
              {c.owner} owns this{c.handover ? `. ${c.handover} picks it up at the 09:00 handover.` : "."}</p>
          )}
          {assign && <Proposal a={assign} busy={busy} onAct={(action) => act(assign.id, () => post(`/approvals/${assign.id}`, { action }))} />}
          {client && <Proposal a={client} busy={busy} quote onAct={(action) => act(client.id, () => post(`/approvals/${client.id}`, { action }))} />}
        </section>
      )}

      {twin && (
        <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-line-strong px-4 py-3 text-sm">
          <span className="text-text-2"><span className="font-mono">{twin.id}</span> &ldquo;{twin.title}&rdquo; looks like the same failure.</span>
          <Button size="sm" disabled={busy !== null} onClick={() => act("merge", () => post(`/clusters/${c.id}/merge/${twin.id}`))}>Merge into {c.id}</Button>
        </section>
      )}
    </article>
  );
}

function sameFields(o: ClusterRow, c: ClusterDetail) {
  // Rows carry only the title; a cluster with no theory yet and a shared field list comes from the API.
  return (o as ClusterRow & { fields?: string[] }).fields?.some((f) => c.fields.includes(f)) ?? false;
}

function Proposal({ a, busy, onAct, quote = false }: { a: Approval; busy: string | null; onAct: (x: "approve" | "dismiss") => void; quote?: boolean }) {
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-sm"><span className="mr-2 font-mono text-[11px] text-muted">{a.agent === "dispatcher" ? "DP" : "RC"}</span>{a.title}</span>
        <div className="flex gap-2">
          <Button size="sm" variant="quiet" disabled={busy !== null} onClick={() => onAct("dismiss")}>Dismiss</Button>
          <Button size="sm" disabled={busy !== null} onClick={() => onAct("approve")}>{a.kind === "client_update" ? "Approve for sending" : "Approve"}</Button>
        </div>
      </div>
      {a.detail && <p className={`text-sm text-muted ${quote ? "border-l-2 border-line-strong pl-3" : ""}`}>{a.detail}</p>}
    </div>
  );
}

function formatSql(sql: string) {
  return sql.replace(/\s+(FROM|WHERE|AND)\s+/g, "\n$1 ");
}

function FocusSkeleton() {
  return (
    <div className="mx-auto flex w-full max-w-[860px] animate-pulse flex-col gap-4">
      <div className="h-4 w-48 rounded bg-raised" />
      <div className="h-8 w-3/4 rounded bg-raised" />
      <div className="h-40 rounded-xl bg-panel" />
    </div>
  );
}
