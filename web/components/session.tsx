"use client";

import Link from "next/link";
import { CheckCircle, HandPalm, Paperclip, Robot, XCircle } from "@phosphor-icons/react";
import { usePoll } from "@/lib/api";
import { Initials } from "./ui";

type T = { id: string; charter: string; target: string; status: string; owner_kind: string; claimed_by: string | null; claimed_name: string;
  source: string; help: string; result: string; bug: string | null; evidence: number; unread_ask: boolean };
type S = { role: string; me: string; tests: T[]; testers: { id: string; name: string; shift: string }[]; recipes: number };

/** One test session, two kinds of testers. The agent runs what it can and hands the rest to people. */
export function TestSession({ rid, pid }: { rid: string; pid: string }) {
  const { data } = usePoll<S>(`/releases/${rid}/session`, 1500);
  if (!data) return <div className="h-64 animate-pulse rounded-xl bg-panel" />;
  const agent = data.tests.filter((t) => t.owner_kind === "agent");
  const human = data.tests.filter((t) => t.owner_kind === "human");
  const mine = human.filter((t) => t.claimed_by === data.me && ["todo", "running"].includes(t.status));
  const href = (t: T) => `/p/${pid}/r/${rid}/t/${t.id}`;

  if (data.tests.length === 0) {
    return <p className="rounded-xl border border-dashed border-line-strong p-8 text-sm text-muted">No test plan yet. Run the agent tester: it plans checks from the pre-mortem, runs the ones it can, and hands the rest to your testers.</p>;
  }
  return (
    <div className="flex flex-col gap-8">
      {mine.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-accent/50 bg-accent-soft/40 px-5 py-3 text-sm">
          <HandPalm size={16} className="text-accent" />
          <span className="font-medium">The agent asked you to run {mine.length} check{mine.length > 1 ? "s" : ""}.</span>
          {mine.slice(0, 3).map((t) => <Link key={t.id} href={href(t)} className="text-accent hover:underline">{t.charter.slice(0, 44)}</Link>)}
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <Lane title="Agent tester" subtitle="Checks it can run safely: data and API" icon={<Robot size={16} className="text-agent" />}>
          {agent.map((t) => <Row key={t.id} t={t} href={href(t)} />)}
        </Lane>
        <Lane title="Human testers" subtitle="UI, devices, write actions and judgement. The agent drafts the steps and asks." icon={<HandPalm size={16} className="text-ok" />}>
          {data.testers.map((p) => {
            const theirs = human.filter((t) => t.claimed_by === p.id);
            return (
              <div key={p.id} className="flex flex-col gap-2">
                <p className="flex items-center gap-2 pt-1 text-xs text-muted"><Initials name={p.name} size={20} />{p.name} ({theirs.filter((t) => ["todo", "running"].includes(t.status)).length} open)</p>
                {theirs.map((t) => <Row key={t.id} t={t} href={href(t)} />)}
              </div>
            );
          })}
          {human.filter((t) => !t.claimed_by).map((t) => <Row key={t.id} t={t} href={href(t)} />)}
        </Lane>
      </div>
      <p className="text-xs text-muted">{data.recipes} recipe{data.recipes === 1 ? "" : "s"} taught by testers so far. The agent reuses them and says where each step came from.</p>
    </div>
  );
}

function Lane({ title, subtitle, icon, children }: { title: string; subtitle: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-xl bg-panel/60 p-3">
      <header className="flex flex-col gap-0.5 px-1">
        <h3 className="flex items-center gap-2 text-sm font-semibold">{icon}{title}</h3>
        <p className="text-[11px] text-muted">{subtitle}</p>
      </header>
      {children}
    </section>
  );
}

function Row({ t, href }: { t: T; href: string }) {
  const icon = t.status === "passed" ? <CheckCircle size={16} className="text-ok" /> : t.status === "failed" ? <XCircle size={16} className="text-bad" />
    : t.status === "running" ? <span className="h-3 w-3 animate-pulse rounded-full bg-accent" /> : <span className="h-3 w-3 rounded-full border border-line-strong" />;
  return (
    <Link href={href} className="enter flex items-start gap-3 rounded-lg border border-line bg-bg px-3 py-2.5 hover:border-line-strong">
      <span className="mt-0.5 flex w-4 justify-center">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="line-clamp-2 text-[13px] leading-snug">{t.charter}</span>
        <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
          <span className={t.source.startsWith("taught") ? "text-ok" : ""}>{t.source}</span>
          {t.result && <span className={t.status === "failed" ? "text-bad" : ""}>{t.result.slice(0, 50)}</span>}
          {t.evidence > 0 && <span className="flex items-center gap-1"><Paperclip size={11} />{t.evidence}</span>}
          {t.bug && <span className="text-bad">bug filed</span>}
          {t.owner_kind === "human" && t.status === "todo" && t.help && <span className="text-accent">agent asked for help</span>}
        </span>
      </span>
    </Link>
  );
}
