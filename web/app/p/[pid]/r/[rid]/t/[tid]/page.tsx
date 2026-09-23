"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { HandPalm, Robot } from "@phosphor-icons/react";
import { post, usePoll } from "@/lib/api";
import { Shell } from "@/components/shell";
import { Badge, Button } from "@/components/ui";
import { Attachment, Evidence } from "@/components/evidence";
import { Note, Thread } from "@/components/thread";

type Room = {
  test: { id: string; charter: string; target: string; status: string; owner_kind: string; claimed_by: string | null; claimed_name: string;
    how: string[]; source: string; help: string; result: string; bug: string | null; project: string; release: string };
  role: string; me: string; evidence: Attachment[]; thread: Note[]; testers: { id: string; name: string }[];
};

export default function TestRoom() {
  const { pid, rid, tid } = useParams<{ pid: string; rid: string; tid: string }>();
  const { data: r, error, reload } = usePoll<Room>(`/tests/${tid}`, 2000);
  if (!r) return <Shell crumbs={[{ label: "Test" }]}><p className="text-sm text-muted">{error ?? "Loading"}</p></Shell>;
  const t = r.test;
  const canTest = ["owner", "lead", "engineer", "tester"].includes(r.role);

  return (
    <Shell wide crumbs={[{ label: "Release", href: `/p/${pid}/r/${rid}` }, { label: t.charter.slice(0, 36) }]}>
      <div className="flex flex-col gap-10">
        <header className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <Badge tone={t.status === "passed" ? "ok" : t.status === "failed" ? "bad" : "accent"}>{t.status === "todo" ? "To do" : t.status}</Badge>
            <span className="flex items-center gap-1.5">
              {t.owner_kind === "agent" ? <><Robot size={14} className="text-agent" /> Agent tester</> : <><HandPalm size={14} className="text-ok" /> {t.claimed_name || "Unassigned"}</>}
            </span>
            {t.bug && <Link href={`/p/${pid}/r/${rid}/b/${t.bug}`} className="text-bad hover:underline">Bug filed from this check</Link>}
          </div>
          <h1 className="max-w-[52ch] text-2xl font-semibold leading-snug tracking-tight">{t.charter}</h1>
        </header>

        {t.owner_kind === "human" && t.help && t.status !== "passed" && t.status !== "failed" && (
          <section className="flex items-start gap-3 rounded-xl border border-accent/50 bg-accent-soft/40 px-5 py-4">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-agent-soft text-agent"><Robot size={16} /></span>
            <div className="text-sm">
              <p className="font-medium">Why the agent needs a person here</p>
              <p className="text-text-2">{t.help}</p>
            </div>
          </section>
        )}

        <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="flex flex-col gap-10">
            <Steps r={r} canTest={canTest} reload={reload} />
            <Evidence ownerType="test" ownerId={t.id} items={r.evidence} canAdd={canTest} onChange={reload} />
          </div>
          <Thread ownerType="test" ownerId={t.id} notes={r.thread} canWrite={canTest || r.role === "support"} onChange={reload} />
        </div>
      </div>
    </Shell>
  );
}

function Steps({ r, canTest, reload }: { r: Room; canTest: boolean; reload: () => void }) {
  const t = r.test;
  const [steps, setSteps] = useState(t.how.join("\n"));
  const [notes, setNotes] = useState("");
  const [save, setSave] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const open = ["todo", "running"].includes(t.status);
  const human = t.owner_kind === "human";
  const finish = async (status: "passed" | "failed") => {
    setBusy(true); setErr(null);
    try {
      await post(`/tests/${t.id}/finish`, { status, notes, steps: steps.split("\n").map((s) => s.trim()).filter(Boolean), save_recipe: save });
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold">How to test it</h2>
        <span className={`text-xs ${t.source.startsWith("taught") ? "text-ok" : "text-muted"}`}>{t.source}</span>
      </div>
      {human && open && canTest ? (
        <>
          <label htmlFor="steps" className="text-xs text-muted">The agent&apos;s draft. Fix it as you go: your version teaches the agent.</label>
          <textarea id="steps" className="h-36 rounded-lg border border-line-strong bg-raised p-3 font-mono text-[12px] leading-relaxed text-text focus:border-accent focus:outline-none"
            value={steps} onChange={(e) => setSteps(e.target.value)} />
        </>
      ) : (
        <ol className="flex flex-col gap-2">
          {t.how.map((s, i) => (
            <li key={i} className="grid grid-cols-[24px_minmax(0,1fr)] gap-3 text-sm">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-raised font-mono text-[11px] text-text-2">{i + 1}</span>{s}
            </li>
          ))}
        </ol>
      )}
      {t.result && <p className={`text-sm ${t.status === "failed" ? "text-bad" : t.status === "passed" ? "text-ok" : "text-muted"}`}>{t.result}</p>}

      {human && open && canTest && (
        <div className="flex flex-col gap-3 rounded-xl border border-line p-4">
          {t.claimed_by !== r.me && (
            <Button size="sm" className="self-start" disabled={busy} onClick={async () => { await post(`/tests/${t.id}/claim`); reload(); }}>
              {t.claimed_by ? `Take it over from ${t.claimed_name.split(" ")[0]}` : "I'll take this"}
            </Button>
          )}
          <label htmlFor="what" className="text-xs text-muted">What you saw</label>
          <input id="what" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Theme reset to light for a LEGACY_DARK account"
            className="h-10 rounded-lg border border-line-strong bg-raised px-3 text-sm text-text placeholder:text-muted focus:border-accent focus:outline-none" />
          <label className="flex items-center gap-2 text-sm text-text-2">
            <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} />
            Save my steps so the agent can do this next time
          </label>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" size="sm" disabled={busy} onClick={() => finish("failed")}>Failed, file a bug</Button>
            <Button size="sm" disabled={busy} onClick={() => finish("passed")}>Passed</Button>
          </div>
          <p className="text-xs text-muted">Record your screen or attach screenshots first. Everything attached goes onto the bug.</p>
          {err && <p className="text-xs text-bad">{err}</p>}
        </div>
      )}
      {!human && <p className="flex items-center gap-2 text-xs text-muted"><Robot size={14} className="text-agent" />The agent tester ran this on the staging replica. Its request and result are in the evidence.</p>}
    </section>
  );
}
