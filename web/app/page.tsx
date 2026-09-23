"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Plus } from "@phosphor-icons/react";
import { usePoll } from "@/lib/api";
import { Shell, StageBar, StageDot } from "@/components/shell";

type Home = {
  me: { name: string };
  org: { name: string; kind: string };
  clients: { id: string; name: string }[];
  projects: { id: string; name: string; client: string | null; client_name: string | null; role: string; description: string;
    releases: { id: string; name: string; stage: string; window_start: string }[]; repos: { full_name: string; status: string }[] }[];
  upcoming: { id: string; name: string; stage: string; window_start: string; project: string; project_id: string }[];
  can_create: boolean;
  needs_org?: boolean;
};

const STAGES = ["plan", "build", "staging", "go_no_go", "live", "closed"];

export default function HomePage() {
  const router = useRouter();
  const { data: h, error } = usePoll<Home>("/home", 5000);
  useEffect(() => { if (h?.needs_org) router.replace("/welcome"); }, [h, router]);

  if (!h || h.needs_org) return <Shell crumbs={[]}><p className="text-sm text-muted">{error ?? "Loading"}</p></Shell>;

  const groups = new Map<string, Home["projects"]>();
  for (const p of h.projects) {
    const key = h.org.kind === "agency" ? p.client_name ?? "Internal" : "Projects";
    groups.set(key, [...(groups.get(key) ?? []), p]);
  }

  return (
    <Shell crumbs={[{ label: h.org.name }]}
      actions={<span className="flex items-center gap-3 whitespace-nowrap sm:gap-4"><Link href="/people" className="text-[13px] text-muted hover:text-text">People</Link><Link href="/team" className="text-[13px] text-muted hover:text-text">Knowledge</Link>{h.can_create && <Link href="/projects/new" aria-label="New project" className="inline-flex h-9 items-center gap-2 rounded-lg bg-accent px-2.5 text-sm font-medium text-accent-ink hover:brightness-110 sm:px-3"><Plus size={14} /><span className="hidden sm:inline">New project</span></Link>}</span>}>
      <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex flex-col gap-10">
          {h.projects.length === 0 && (
            <div className="flex flex-col items-start gap-3 rounded-xl border border-dashed border-line-strong p-8">
              <h1 className="text-xl font-semibold">No projects yet</h1>
              <p className="text-sm text-muted">{h.can_create ? "Create a project and connect its GitHub repo. Context builds in the background." : "Ask an org admin to add you to a project."}</p>
            </div>
          )}
          {[...groups.entries()].map(([client, projects]) => (
            <section key={client} className="flex flex-col gap-3">
              <h2 className="text-xs font-semibold text-muted">{client}</h2>
              <div className="flex flex-col divide-y divide-line rounded-xl border border-line">
                {projects.map((p) => {
                  const active = p.releases.find((r) => r.stage !== "closed");
                  return (
                    <Link key={p.id} href={`/p/${p.id}`} className="flex flex-col gap-2 px-5 py-4 hover:bg-panel">
                      <div className="flex items-center justify-between gap-4">
                        <span className="font-medium">{p.name}</span>
                        <span className="text-xs text-muted">{p.role}</span>
                      </div>
                      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted">
                        {active ? (
                          <span className="flex items-center gap-2"><StageBar stages={STAGES} current={active.stage} /> {active.name}, <StageDot stage={active.stage} /></span>
                        ) : <span>No release in progress</span>}
                        <span>{p.repos.length ? p.repos.map((r) => r.full_name).join(", ") : "No repo connected"}</span>
                      </div>
                    </Link>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
        <aside className="flex flex-col gap-3">
          <h2 className="text-xs font-semibold text-muted">Scheduled deployments</h2>
          {h.upcoming.length === 0 && <p className="text-sm text-muted">Nothing scheduled.</p>}
          {h.upcoming.map((r) => (
            <Link key={r.id} href={`/p/${r.project_id}/r/${r.id}`} className="flex flex-col gap-1 rounded-lg border border-line px-4 py-3 hover:bg-panel">
              <span className="font-mono text-xs text-accent">{r.window_start ? r.window_start.replace("T", " ") : "No window yet"}</span>
              <span className="text-sm">{r.name}</span>
              <span className="flex gap-1 text-xs text-muted">{r.project}, <StageDot stage={r.stage} /></span>
            </Link>
          ))}
        </aside>
      </div>
    </Shell>
  );
}
