"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Copy } from "@phosphor-icons/react";
import { del, get, post, usePoll } from "@/lib/api";
import { Shell, StageDot, inputCls } from "@/components/shell";
import { Answer, BriefQ } from "@/components/setup";
import { Button, Initials } from "@/components/ui";

type T = {
  track: { id: string; name: string; kind: string; about: string; members: { id: string; name: string }[] };
  sentence: string; areas: { id: string; name: string }[];
  incidents: { id: string; title: string; status: string; severity: string; opened_at: number }[];
  bugs: { id: string; title: string; stage: string; priority: string; release: string }[];
  releases: { id: string; name: string; stage: string; window: string; areas: string[] }[];
  notes: { id: string; title: string; by: string; at: number }[];
  questions: BriefQ[]; mine: boolean; can_edit: boolean; project: { id: string; name: string };
};

/** One team's view of the project: what affects its areas, in plain words, and what it knows that nobody else wrote down. */
export default function TrackPage() {
  const { pid, tid } = useParams<{ pid: string; tid: string }>();
  const { data: t, reload } = usePoll<T>(`/tracks/${tid}`, 10000);
  if (!t) return <Shell crumbs={[{ label: "Track" }]}><div className="h-40 animate-pulse rounded-2xl bg-panel" /></Shell>;
  const business = t.track.kind === "business";
  const answered = t.questions.filter((q) => q.answer).length;
  return (
    <Shell crumbs={[{ label: t.project.name, href: `/p/${pid}` }, { label: t.track.name }]}>
      <div className="flex flex-col gap-9 md:px-10">
        <header className="flex flex-col gap-2">
          <h1 className="text-[30px] font-semibold tracking-tight">{t.track.name}</h1>
          <p className="max-w-[72ch] text-base text-[#b8b7b2]">{t.sentence}</p>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
            {t.areas.length > 0 && <span>Owns {t.areas.map((a) => a.name).join(", ")}</span>}
            <span className="flex -space-x-1.5">{t.track.members.slice(0, 6).map((m) => <Initials key={m.id} name={m.name} size={22} />)}</span>
            <Link href={`/p/${pid}?view=setup#tracks`} className="hover:text-text">Edit team</Link>
          </div>
        </header>

        {t.incidents.length > 0 && (
          <section className="flex flex-col gap-2.5">
            <h2 className="text-[13px] font-semibold text-accent">Affecting you now</h2>
            {t.incidents.map((i) => <Incident key={i.id} i={i} tid={tid} pid={pid} business={business} />)}
          </section>
        )}

        <div className="grid gap-8 lg:grid-cols-2">
          <section className="flex flex-col gap-3">
            <h2 className="text-[15px] font-semibold">Coming up</h2>
            {t.releases.length === 0 ? <p className="text-[13px] text-muted">No release changes your areas right now.</p> : t.releases.map((r) => (
              <Link key={r.id} href={`/p/${pid}/r/${r.id}`} className="flex flex-col gap-1 rounded-xl border border-line px-4 py-3 hover:border-line-strong">
                <span className="text-sm font-medium">{r.name}</span>
                <span className="flex flex-wrap gap-1 text-xs text-muted"><StageDot stage={r.stage} />{r.window ? `, deploys ${r.window.replace("T", " ")}` : ""}. Changes {r.areas.map((a) => t.areas.find((x) => x.id === a)?.name ?? a).join(", ")}.</span>
              </Link>
            ))}
          </section>
          <section className="flex flex-col gap-3">
            <h2 className="text-[15px] font-semibold">Being fixed</h2>
            {t.bugs.length === 0 ? <p className="text-[13px] text-muted">No open bugs in your areas.</p> : t.bugs.map((b) => (
              <Link key={b.id} href={`/p/${pid}/r/${b.release}/b/${b.id}`} className="flex items-baseline gap-3 rounded-xl border border-line px-4 py-2.5 hover:border-line-strong">
                <span className="min-w-0 flex-1 truncate text-sm">{b.title}</span>
                <span className="text-xs text-muted">{b.stage.replace("_", " ")}</span>
              </Link>
            ))}
          </section>
        </div>

        <section className="flex flex-col gap-3">
          <div className="flex items-baseline justify-between">
            <h2 className="text-[15px] font-semibold">What only this team knows</h2>
            <span className="text-xs text-muted">{answered} of {t.questions.length} answered</span>
          </div>
          <p className="-mt-1 max-w-[75ch] text-[13px] text-muted">Agents use these when work lands in your areas: deciding if a problem is real, explaining a bug, writing a customer update.</p>
          <div className="flex flex-col divide-y divide-line rounded-2xl border border-line">
            {t.questions.map((q) => <Answer key={q.qid} url={`/tracks/${tid}/brief/${q.qid.split("/").pop()}`} q={q} can={t.can_edit} onSaved={reload} />)}
          </div>
        </section>

        <Notes t={t} tid={tid} reload={reload} />
      </div>
    </Shell>
  );
}

function Incident({ i, tid, pid, business }: { i: T["incidents"][0]; tid: string; pid: string; business: boolean }) {
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-[#4a3a1e] bg-[#1a1712] px-5 py-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="min-w-0 flex-1 text-[15px] font-medium">{i.title}</span>
        <span className="text-xs text-muted">{i.status === "proposed" ? "waiting for someone to open it" : i.status}, since {new Date(i.opened_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
      </div>
      <div className="flex flex-wrap gap-2">
        <Link href={`/p/${pid}/i/${i.id}`} className="inline-flex h-9 items-center rounded-lg border border-line-strong px-3 text-[13px] hover:bg-raised">Open the war room</Link>
        {business && !text && <button type="button" disabled={busy} onClick={async () => { setBusy(true); setText((await get(`/tracks/${tid}/incidents/${i.id}/update`)).text); setBusy(false); }}
          className="inline-flex h-9 items-center rounded-lg bg-accent px-3.5 text-[13px] font-semibold text-accent-ink">{busy ? "Writing" : "Draft a customer update"}</button>}
      </div>
      {text && (
        <div className="flex flex-col gap-2">
          <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} aria-label="Customer update"
            className="rounded-lg border border-line-strong bg-bg px-3 py-2 text-sm leading-relaxed focus:border-text-2 focus:outline-none" />
          <div className="flex items-center gap-3">
            <Button size="sm" onClick={() => navigator.clipboard.writeText(text)}><Copy size={12} />Copy</Button>
            <span className="text-[11px] text-muted">A draft. Nothing is sent: check it and send it yourself.</span>
          </div>
        </div>
      )}
    </div>
  );
}

function Notes({ t, tid, reload }: { t: T; tid: string; reload: () => void }) {
  const [f, setF] = useState({ title: "", text: "" });
  const [err, setErr] = useState<string | null>(null);
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-[15px] font-semibold">Notes and runbooks</h2>
      <p className="-mt-1 text-[13px] text-muted">Paste a runbook, a decision or a glossary. Only this team&apos;s agents and leads see it; the rest of the project doesn&apos;t.</p>
      {t.notes.length > 0 && (
        <ul className="flex flex-col divide-y divide-line rounded-2xl border border-line">
          {t.notes.map((n) => (
            <li key={n.id} className="flex items-center gap-3 px-5 py-2.5 text-sm">
              <span className="flex-1">{n.title}</span><span className="text-xs text-muted">{n.by}</span>
              {t.can_edit && <button type="button" className="text-xs text-muted hover:text-text" onClick={async () => { await del(`/tracks/${tid}/notes/${n.id}`); reload(); }}>Remove</button>}
            </li>
          ))}
        </ul>
      )}
      {t.can_edit && (
        <form className="flex max-w-[760px] flex-col gap-2" onSubmit={async (e) => {
          e.preventDefault(); setErr(null);
          try { await post(`/tracks/${tid}/notes`, f); setF({ title: "", text: "" }); reload(); } catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
        }}>
          <label className="sr-only" htmlFor="n-title">Title</label>
          <input id="n-title" className={inputCls} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="Refund runbook" />
          <label className="sr-only" htmlFor="n-text">Text</label>
          <textarea id="n-text" rows={5} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} placeholder="Paste it here. Headings split it into sections agents can cite."
            className="rounded-lg border border-line-strong bg-raised px-3 py-2 text-sm leading-relaxed placeholder:text-faint focus:border-text-2 focus:outline-none" />
          <div className="flex items-center gap-3"><Button type="submit" size="sm" disabled={f.text.trim().length < 20}>Add</Button>{err && <span className="text-xs text-bad">{err}</span>}</div>
        </form>
      )}
    </section>
  );
}
