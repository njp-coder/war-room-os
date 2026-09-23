"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { post, usePoll } from "@/lib/api";
import { Shell } from "@/components/shell";
import { BriefInterview, CapabilityMap, Chapter, ConnectTiles, Rail, RailItem } from "@/components/setup-map";
import { LiveLine, NeedsYou, NowData, QueueBug, ReleaseCard, ScheduleRelease, TeamRow, TeamToday } from "@/components/now";
import { Experts, Inline, Tracks, Guardrails, People, ProjectData, Repo, Settings, SetupData, SetupProgress, Sources } from "@/components/setup";

export default function ProjectPage() {
  return <Suspense fallback={null}><Project /></Suspense>;
}

function Project() {
  const { pid } = useParams<{ pid: string }>();
  const view = useSearchParams().get("view") === "setup" ? "setup" : "now";
  const router = useRouter();
  const { data: d, error, reload } = usePoll<ProjectData>(`/projects/${pid}`, 5000);
  const { data: now, reload: reloadNow } = usePoll<NowData>(`/projects/${pid}/now`, 3000);
  const { data: setup, reload: reloadSetup } = usePoll<SetupData>(view === "setup" ? `/projects/${pid}/setup` : null, 10000);
  const { data: tr } = usePoll<{ tracks: { id: string; name: string }[]; mine: string[] }>(`/projects/${pid}/tracks`, 60000);

  if (!d || !now) return <Shell crumbs={[{ label: "Project" }]}><div className="flex flex-col gap-4"><div className="h-10 w-72 animate-pulse rounded-lg bg-panel" /><div className="h-5 w-[480px] max-w-full animate-pulse rounded bg-panel" />{error && <p className="text-sm text-muted">{error}</p>}</div></Shell>;
  const lead = d.role === "owner" || d.role === "lead";
  const guest = d.role === "client_guest";
  const sentence = view === "setup" ? setup?.sentence : now.sentence;
  const go = (v: string) => router.replace(v === "setup" ? `/p/${pid}?view=setup` : `/p/${pid}`, { scroll: false });

  return (
    <Shell crumbs={[{ label: d.project.client_name ?? "Projects", href: "/" }, { label: d.project.name }]}
      actions={!guest && <nav className="hidden items-center gap-5 text-[13px] text-muted md:flex">
        {(tr?.tracks ?? []).filter((t) => tr!.mine.includes(t.id)).map((t) => <Link key={t.id} href={`/p/${pid}/tracks/${t.id}`} className="text-text-2 hover:text-text">{t.name}</Link>)}
        <Link href={`/p/${pid}/context`} className="hover:text-text">Context</Link>
        <Link href={`/p/${pid}/schema`} className="hover:text-text">Schema</Link>
        <Link href={`/p/${pid}/agents`} className="hover:text-text">How work flows</Link>
      </nav>}>
      <div className="flex flex-col gap-9 md:px-10 lg:px-20">
        <header className="flex flex-col gap-3">
          <div className="flex flex-wrap items-end gap-6">
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <h1 className="text-[34px] font-semibold leading-tight tracking-tight">{d.project.name}</h1>
              <p className="max-w-[70ch] text-base text-[#b8b7b2]">{sentence ?? " "}</p>
            </div>
            {!guest && (
              <nav className="flex gap-1 rounded-[10px] border border-line bg-panel p-1" aria-label="View">
                {(["now", "setup"] as const).map((v) => (
                  <button key={v} type="button" onClick={() => go(v)} aria-current={view === v ? "page" : undefined}
                    className={`flex h-9 items-center gap-2 rounded-[7px] px-4 text-[13px] ${view === v ? "bg-[#2b2d33] font-medium text-text" : "text-muted hover:text-text"}`}>
                    {v === "now" ? "Now" : "Setup"}
                    {v === "setup" && now.setup_todo > 0 && <span className="font-mono text-[11px] text-muted">{now.setup_todo} to do</span>}
                  </button>
                ))}
              </nav>
            )}
          </div>
          {view === "now" && <LiveLine pid={pid} />}
          {view === "setup" && setup && <SetupProgress items={setup.items} />}
        </header>

        {view === "now" ? <NowView pid={pid} d={d} now={now} lead={lead} reload={() => { reload(); reloadNow(); }} />
          : setup ? <SetupView pid={pid} d={d} setup={setup} lead={lead} reload={() => { reload(); reloadSetup(); reloadNow(); }} />
            : <div className="h-64 animate-pulse rounded-2xl bg-panel" />}
      </div>
    </Shell>
  );
}

function NowView({ pid, d, now, lead, reload }: { pid: string; d: ProjectData; now: NowData; lead: boolean; reload: () => void }) {
  const [scheduling, setScheduling] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const assign = async (bug: QueueBug, to: TeamRow) => {
    setErr(null);
    try { await post(`/bugs/${bug.id}/reassign`, { to: to.id }); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    reload();
  };
  const others = d.releases.filter((r) => r.id !== now.release?.id && r.stage !== "closed");
  return (
    <>
      <NeedsYou pid={pid} now={now} reload={reload} />
      {scheduling && <ScheduleRelease pid={pid} onDone={() => { setScheduling(false); reload(); }} />}
      <section className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_380px]">
        <ReleaseCard pid={pid} now={now} others={others} lead={lead} onSchedule={() => setScheduling(true)} />
        <div className="flex flex-col gap-2">
          <TeamToday team={now.team} queue={now.release?.queue ?? []} canAssign={lead} onAssign={assign} />
          {err && <p className="text-xs text-bad">{err}</p>}
        </div>
      </section>
      {!now.needs.length && <OpenWarRoom pid={pid} />}
    </>
  );
}

/** People can always open a war room by hand; the agent is not the only trigger. */
function OpenWarRoom({ pid }: { pid: string }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const router = useRouter();
  if (!open) return <button type="button" onClick={() => setOpen(true)} className="self-start text-[13px] text-muted hover:text-text">Something wrong in production? Open a war room</button>;
  return (
    <form className="enter flex flex-wrap items-center gap-2" onSubmit={async (e) => {
      e.preventDefault();
      if (!title.trim()) return;
      const { id } = await post(`/projects/${pid}/incidents`, { title, severity: "high" });
      router.push(`/p/${pid}/i/${id}`);
    }}>
      <label htmlFor="wr-title" className="sr-only">What&apos;s wrong</label>
      <input id="wr-title" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Checkout is slow for everyone"
        className="h-10 w-[380px] max-w-full rounded-lg border border-line-strong bg-raised px-3 text-sm placeholder:text-muted focus:border-text-2 focus:outline-none" />
      <button type="submit" className="h-10 rounded-lg bg-accent px-4 text-[13px] font-semibold text-accent-ink">Open war room</button>
      <button type="button" onClick={() => setOpen(false)} className="h-10 px-3 text-[13px] text-muted hover:text-text">Cancel</button>
    </form>
  );
}

function SetupView({ pid, d, setup, lead, reload }: { pid: string; d: ProjectData; setup: SetupData; lead: boolean; reload: () => void }) {
  const item = (id: string) => setup.items.find((i) => i.id === id);
  const hasSources = !!item("sources")?.done;
  const jump = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  const connectIds = ["repo", "staging_db", "staging_url", "monitor_db", "sources"];
  const connected = connectIds.filter((i) => item(i)?.done).length;
  const owners = item("codeowners");
  const rail: RailItem[] = [
    { id: "connect", label: "Connect", count: `${connected}/${connectIds.length}`, done: connected === connectIds.length },
    { id: "teach", label: "Teach", count: item("brief")?.detail?.split(" answered")[0] ?? undefined, done: item("brief")?.done },
    { id: "team", label: "Team", done: item("people")?.done && owners?.done },
    { id: "rules", label: "Rules" },
    { id: "project", label: "Project" },
  ];
  return (
    <>
      <CapabilityMap items={setup.items} onJump={jump} />
      <div className="grid gap-10 lg:grid-cols-[168px_minmax(0,1fr)]">
        <Rail items={rail} />
        <div className="flex min-w-0 flex-col gap-16">
          <Chapter id="connect" title="Connect" lead="Where agents read from. Everything is read-only, and staging is the only place they ever call.">
            <ConnectTiles pid={pid} items={setup.items} lead={lead} reload={reload} onJump={jump} />
            <div id="logs" className="scroll-mt-20"><Sources pid={pid} lead={lead} onChange={reload} /></div>
          </Chapter>
          <Chapter id="teach" title="Teach" lead="What the code can't say: what matters, when it's busy, what counts as broken. Then the experts who learn from all of it.">
            <BriefInterview pid={pid} />
            <Experts pid={pid} lead={lead} canTeach={["owner", "lead", "engineer"].includes(d.role)} />
          </Chapter>
          <Chapter id="team" title="Team" lead="Who works here, who owns what, and which team hears about problems in each area.">
            <People d={d} pid={pid} lead={lead} reload={reload} />
            {owners && !owners.done && lead && (
              <div className="flex flex-col gap-3 rounded-2xl border border-line bg-[#111214] p-5">
                <span className="text-[15px] font-medium">Code owners</span>
                <p className="text-[13px] text-muted">{owners.why}</p>
                <Inline pid={pid} item={owners} reload={reload} />
              </div>
            )}
            <Tracks pid={pid} />
          </Chapter>
          <Chapter id="rules" title="Rules" lead="When agents may act on their own, and what they must never do without a person.">
            {hasSources || item("monitor_db")?.done ? <Guardrails pid={pid} hasSources={hasSources} />
              : <p className="rounded-2xl border border-dashed border-line-strong px-5 py-6 text-[13px] text-muted">Guardrails appear once production monitoring or a log source is connected.</p>}
            {lead && <Settings d={d} pid={pid} reload={reload} />}
          </Chapter>
          <Chapter id="project" title="Project" lead="The repository behind this project and how fresh its context is.">
            <Repo d={d} pid={pid} lead={lead} />
          </Chapter>
        </div>
      </div>
    </>
  );
}
