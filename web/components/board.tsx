"use client";

import Link from "next/link";
import { Robot, User } from "@phosphor-icons/react";
import { usePoll } from "@/lib/api";
import { RelayDots, RelayItem } from "./relay";
import { Initials } from "./ui";

export type Card = {
  id: string; title: string; stage: string; env: string; priority: string; blocker: boolean; reporter_kind: string; reporter: string;
  who: string; who_id: string | null; agreed: boolean; eta: number | null; eta_agent: number | null; eta_owner: number | null; relay: RelayItem[];
};
type Lane = { id: string; name: string; shift: string; worked: number; cap: number; left: number; queued: number; over: boolean;
  fixes: { id: string; title: string; stage: string; hours: number; agreed: boolean }[] };
type BoardData = { role: string; me: string; cards: Card[]; lanes: Lane[] };

const COLUMNS = [
  { key: "agents", title: "Agents working", stages: ["reported", "reproduced", "cause_found"], hint: "Reproducing and finding the cause" },
  { key: "proposed", title: "Waiting for owner", stages: ["proposed"], hint: "An agent proposed who and how long" },
  { key: "fixing", title: "Fixing", stages: ["fixing"], hint: "Owner accepted and agreed an ETA" },
  { key: "fixed", title: "Needs verify", stages: ["fixed"], hint: "A tester checks it on staging" },
  { key: "verified", title: "Verified", stages: ["verified"], hint: "Done" },
];

export function Board({ rid, pid, env }: { rid: string; pid: string; env: string }) {
  const { data } = usePoll<BoardData>(`/releases/${rid}/board?env=${env}`, 1200);
  if (!data) return <div className="h-64 animate-pulse rounded-xl bg-panel" />;
  const mine = data.cards.filter((c) => c.who_id === data.me && c.stage === "proposed");

  return (
    <div className="flex flex-col gap-10">
      {mine.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-accent/50 bg-accent-soft/40 px-5 py-3 text-sm">
          <span className="font-medium">An agent proposed you for {mine.length} fix{mine.length > 1 ? "es" : ""}.</span>
          {mine.map((c) => <Link key={c.id} href={`/p/${pid}/r/${rid}/b/${c.id}`} className="text-accent underline-offset-2 hover:underline">{c.title.slice(0, 48)}</Link>)}
        </div>
      )}

      <div className="grid gap-3 overflow-x-auto md:grid-cols-5">
        {COLUMNS.map((col) => {
          const cards = data.cards.filter((c) => col.stages.includes(c.stage));
          return (
            <section key={col.key} className="flex min-w-[220px] flex-col gap-2 rounded-xl bg-panel/60 p-2">
              <header className="flex items-baseline justify-between px-2 pt-1">
                <h3 className="text-xs font-semibold">{col.title}</h3>
                <span className="font-mono text-xs text-muted">{cards.length}</span>
              </header>
              <p className="px-2 text-[11px] text-muted">{col.hint}</p>
              {cards.map((c) => <BugCard key={c.id} c={c} href={`/p/${pid}/r/${rid}/b/${c.id}`} />)}
              {cards.length === 0 && <div className="mx-1 h-16 rounded-lg border border-dashed border-line" />}
            </section>
          );
        })}
      </div>

      <Capacity lanes={data.lanes} />
    </div>
  );
}

function BugCard({ c, href }: { c: Card; href: string }) {
  const working = ["reported", "reproduced", "cause_found"].includes(c.stage);
  return (
    <Link href={href} className={`enter flex flex-col gap-2.5 rounded-lg border bg-bg p-3 transition-colors hover:border-line-strong ${c.blocker && c.stage !== "verified" ? "border-bad/50" : "border-line"}`}>
      <div className="flex items-start gap-2">
        <span className="mt-0.5 text-muted" title={c.reporter_kind === "agent" ? "Found by the agent tester" : `Reported by ${c.reporter}`}>
          {c.reporter_kind === "agent" ? <Robot size={14} /> : <User size={14} />}
        </span>
        <span className="line-clamp-2 flex-1 text-[13px] leading-snug">{c.title}</span>
      </div>
      <RelayDots relay={c.relay} stage={c.stage} />
      <div className="flex items-center justify-between gap-2">
        {working ? (
          <span className="flex items-center gap-1.5 text-[11px] text-agent"><Robot size={12} className="animate-pulse" /> working</span>
        ) : c.who ? (
          <span className="flex min-w-0 items-center gap-1.5 text-[11px] text-text-2"><Initials name={c.who} size={20} /><span className="truncate">{c.who.split(" ")[0]}</span></span>
        ) : <span className="text-[11px] text-bad">No owner</span>}
        <span className="flex items-center gap-1.5">
          {c.eta ? <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${c.agreed ? "bg-ok-soft text-ok" : "border border-dashed border-line-strong text-muted"}`}
            title={c.agreed ? "ETA agreed by the owner" : "Agent's estimate, not agreed yet"}>{c.agreed ? "" : "~"}{c.eta}h</span> : null}
          <span className={`font-mono text-[10px] ${c.priority === "P0" ? "text-bad" : "text-muted"}`}>{c.priority}</span>
        </span>
      </div>
    </Link>
  );
}

/** Each person's day: hours worked, then fixes queued, against the 12h cap. */
function Capacity({ lanes }: { lanes: Lane[] }) {
  const busy = lanes.filter((l) => l.fixes.length || l.shift === "on");
  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold">Who&apos;s on what today</h3>
        <span className="flex items-center gap-4 text-[11px] text-muted">
          <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded-sm bg-line-strong" />worked</span>
          <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded-sm border border-dashed border-accent" />proposed</span>
          <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded-sm bg-accent" />agreed</span>
          <span className="flex items-center gap-1.5"><span className="h-3 w-px bg-bad" />12h cap</span>
        </span>
      </div>
      <div className="flex flex-col gap-2.5">
        {busy.map((l) => {
          const scale = 16; // hours shown across the bar
          const pct = (h: number) => `${(Math.min(h, scale) / scale) * 100}%`;
          let offset = l.worked;
          return (
            <div key={l.id} className="grid grid-cols-[140px_minmax(0,1fr)_80px] items-center gap-3">
              <span className={`truncate text-sm ${l.shift === "on" ? "" : "text-muted"}`}>{l.name}{l.shift !== "on" ? " (off)" : ""}</span>
              <div className="relative h-7 rounded-md bg-panel">
                <div className="absolute inset-y-0 left-0 rounded-l-md bg-line-strong" style={{ width: pct(l.worked) }} />
                {l.fixes.map((f) => {
                  const left = pct(offset);
                  offset += f.hours;
                  return (
                    <div key={f.id} title={`${f.title} (${f.hours}h${f.agreed ? ", agreed" : ", proposed"})`}
                      className={`absolute inset-y-0.5 overflow-hidden rounded px-1.5 text-[10px] leading-6 ${f.agreed ? "bg-accent text-accent-ink" : "border border-dashed border-accent text-accent"}`}
                      style={{ left, width: `calc(${pct(f.hours)} - 2px)` }}>
                      <span className="truncate">{f.title}</span>
                    </div>
                  );
                })}
                <div className="absolute inset-y-[-3px] w-px bg-bad" style={{ left: pct(l.cap) }} />
              </div>
              <span className={`text-right font-mono text-xs ${l.over ? "text-bad" : "text-muted"}`}>
                {l.over ? `${(l.queued - l.left).toFixed(1)}h over` : `${Math.max(l.left - l.queued, 0).toFixed(1)}h free`}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
