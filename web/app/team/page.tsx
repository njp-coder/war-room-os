"use client";

import Link from "next/link";
import { useState } from "react";
import { Check, FileArrowUp, Trash } from "@phosphor-icons/react";
import { currentUser, post, usePoll } from "@/lib/api";
import { Shell, inputCls } from "@/components/shell";
import { Button } from "@/components/ui";

type Lesson = { id: string; topic: string; text: string; source: string; origin: string | null; status: string; proposed_by: string | null };
type K = {
  experts: { topic: string; name: string; count: number; sources: Record<string, number>; recent: { id: string; text: string; source: string }[] }[];
  pending: Lesson[]; docs: { id: string; name: string; sections: number; by: string; at: number }[];
  projects: { id: string; name: string; topics: string[] }[]; topics: { id: string; label: string }[]; can_manage: boolean;
};
const SOURCE = { handbook: "from the handbook", promoted: "promoted from a project", manual: "written here" } as Record<string, string>;

/** How the whole team does things: org experts every project in the org can lean on. */
export default function Team() {
  const { data: k, reload } = usePoll<K>("/org/knowledge", 30000);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [teach, setTeach] = useState({ topic: "general", text: "" });
  if (!k) return <Shell crumbs={[{ label: "Team knowledge" }]}><div className="h-40 animate-pulse rounded-2xl bg-panel" /></Shell>;
  const label = (t: string) => k.topics.find((x) => x.id === t)?.label ?? "General";

  const upload = async (f: File) => {
    setUploading(true); setErr(null);
    const fd = new FormData();
    fd.append("file", f);
    const r = await fetch("/api/org/handbook", { method: "POST", body: fd, headers: { "x-user": currentUser() ?? "" } });
    if (!r.ok) setErr((await r.json().catch(() => ({}))).detail ?? "Upload failed");
    setUploading(false);
    reload();
  };

  return (
    <Shell crumbs={[{ label: "Team knowledge" }]}>
      <div className="flex flex-col gap-10 md:px-10">
        <header className="flex flex-col gap-2">
          <h1 className="text-[30px] font-semibold tracking-tight">Team knowledge</h1>
          <p className="max-w-[75ch] text-base text-[#b8b7b2]">
            {k.experts.length ? `${k.experts.length} org experts, ${k.experts.reduce((s, e) => s + e.count, 0)} practices.` : "No team practices yet."}
            {" "}Every project gets the experts that match its stack, next to its own area experts. A new project knows how your team works on day one.
          </p>
        </header>

        {k.pending.length > 0 && (
          <section className="flex flex-col gap-2.5">
            <h2 className="text-[13px] font-semibold text-accent">Needs you</h2>
            {k.pending.map((l) => <Pending key={l.id} l={l} label={label(l.topic)} can={k.can_manage} onDone={reload} />)}
          </section>
        )}

        <section className="flex flex-col gap-4">
          <h2 className="text-sm font-semibold">Handbook</h2>
          <p className="-mt-2 max-w-[75ch] text-[13px] text-muted">Standards, runbooks, postmortems. Markdown, text or PDF. Each section is tagged with the technology or area it covers and becomes something the experts cite.</p>
          {k.docs.length > 0 && (
            <div className="flex flex-col divide-y divide-line rounded-2xl border border-line">
              {k.docs.map((d) => (
                <div key={d.id} className="flex items-center gap-3 px-5 py-3 text-sm">
                  <Check size={14} className="text-ok" /><span className="flex-1">{d.name}</span>
                  <span className="text-xs text-muted">{d.sections} sections, added by {d.by}</span>
                  {k.can_manage && <button type="button" aria-label={`Remove ${d.name}`} className="text-muted hover:text-text"
                    onClick={async () => { await fetch(`/api/org/handbook/${d.id}`, { method: "DELETE", headers: { "x-user": currentUser() ?? "" } }); reload(); }}><Trash size={14} /></button>}
                </div>
              ))}
            </div>
          )}
          {k.can_manage && (
            <label className="flex cursor-pointer items-center gap-2 self-start rounded-lg border border-dashed border-line-strong px-4 py-3 text-sm text-text-2 hover:bg-raised">
              <FileArrowUp size={16} />{uploading ? "Reading it" : "Upload a handbook"}
              <input type="file" accept=".md,.markdown,.txt,.pdf" className="sr-only" disabled={uploading} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            </label>
          )}
          {err && <p className="text-xs text-bad">{err}</p>}
        </section>

        <section className="flex flex-col gap-4">
          <h2 className="text-sm font-semibold">Org experts</h2>
          {k.experts.length === 0 && <p className="text-[13px] text-muted">Upload a handbook, write a practice below, or share a proven lesson from a project&apos;s expert.</p>}
          <div className="grid gap-3.5 md:grid-cols-2">
            {k.experts.map((e) => {
              const using = k.projects.filter((p) => p.topics.includes(e.topic));
              return (
                <div key={e.topic} className="flex flex-col gap-2.5 rounded-2xl border border-line p-5">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[15px] font-medium">{e.name}</span>
                    <span className="text-xs text-muted">{e.count} practice{e.count === 1 ? "" : "s"}</span>
                  </div>
                  <p className="text-xs text-muted">{Object.entries(e.sources).map(([s, n]) => `${n} ${SOURCE[s] ?? s}`).join(", ")}</p>
                  {e.recent.map((r) => <p key={r.id} className="line-clamp-2 text-[13px] leading-relaxed text-text-2">{r.text}</p>)}
                  <p className="text-xs text-muted">{using.length ? `Advising ${using.map((p) => p.name).join(", ")}` : "No project uses this yet"}</p>
                </div>
              );
            })}
          </div>
        </section>

        {k.can_manage && (
          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold">Write a practice</h2>
            <form className="flex flex-col gap-2" onSubmit={async (e) => {
              e.preventDefault();
              await post("/org/lessons", teach); setTeach({ ...teach, text: "" }); reload();
            }}>
              <div className="flex flex-wrap gap-2">
                <label className="sr-only" htmlFor="t-topic">Topic</label>
                <select id="t-topic" className={`${inputCls} h-9 w-56`} value={teach.topic} onChange={(e) => setTeach({ ...teach, topic: e.target.value })}>
                  <option value="general">General</option>
                  {k.topics.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                </select>
              </div>
              <label className="sr-only" htmlFor="t-text">Practice</label>
              <textarea id="t-text" rows={2} value={teach.text} onChange={(e) => setTeach({ ...teach, text: e.target.value })}
                placeholder="Add indexes with CREATE INDEX CONCURRENTLY, never inside a migration and never at peak."
                className="max-w-[720px] rounded-lg border border-line-strong bg-raised px-3 py-2 text-sm placeholder:text-faint focus:border-text-2 focus:outline-none" />
              <Button type="submit" size="sm" className="self-start" disabled={teach.text.trim().length < 15}>Add</Button>
            </form>
          </section>
        )}

        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">Which projects get which experts</h2>
          <div className="flex flex-col divide-y divide-line rounded-2xl border border-line">
            {k.projects.map((p) => (
              <div key={p.id} className="grid gap-2 px-5 py-3 md:grid-cols-[180px_minmax(0,1fr)]">
                <Link href={`/p/${p.id}`} className="text-sm hover:underline">{p.name}</Link>
                <span className="text-[13px] text-muted">{p.topics.map(label).join(", ") || "Stack not detected yet"}</span>
              </div>
            ))}
          </div>
          <p className="text-xs text-muted">Detected from each repo&apos;s dependency files, its connected databases and what its code works with.</p>
        </section>
      </div>
    </Shell>
  );
}

/** A project lesson proposed for the whole team. Already rewritten without client specifics; edit before approving if needed. */
function Pending({ l, label, can, onDone }: { l: Lesson; label: string; can: boolean; onDone: () => void }) {
  const [text, setText] = useState(l.text);
  const decide = async (approve: boolean) => { await post(`/org/lessons/${l.id}/decide`, { approve, text }); onDone(); };
  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-[#4a3a1e] bg-[#1a1712] px-5 py-4">
      <span className="text-xs text-muted">{l.proposed_by} wants to share this with every project, as a {label} practice. It was rewritten without client names or data. Check nothing identifying is left.</span>
      <label className="sr-only" htmlFor={`p-${l.id}`}>Practice</label>
      <textarea id={`p-${l.id}`} rows={2} value={text} onChange={(e) => setText(e.target.value)} disabled={!can}
        className="rounded-lg border border-line-strong bg-bg px-3 py-2 text-sm focus:border-text-2 focus:outline-none" />
      {can ? (
        <div className="flex gap-2">
          <button type="button" onClick={() => decide(true)} className="inline-flex h-9 items-center rounded-lg bg-accent px-3.5 text-[13px] font-semibold text-accent-ink">Share with the team</button>
          <Button size="sm" variant="quiet" onClick={() => decide(false)}>Don&apos;t share</Button>
        </div>
      ) : <span className="text-xs text-muted">Waiting for an org admin.</span>}
    </div>
  );
}
