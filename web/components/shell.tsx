"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { setCurrentUser, signOut, usePoll } from "@/lib/api";
import { Initials } from "./ui";

type Me = { id: string; name: string; title: string; org_role: string; github: string | null; avatar: string | null; demo: boolean };

export function Shell({ crumbs, actions, children, wide = false }: {
  crumbs: { label: string; href?: string }[]; actions?: ReactNode; children: ReactNode; wide?: boolean;
}) {
  const router = useRouter();
  const { data: me } = usePoll<Me>("/me", 60000);
  return (
    <div className="min-h-[100dvh]">
      {me?.demo && (
        <div className="bg-[#2a2216] px-4 py-1.5 text-center text-xs text-[#e8c88a]">
          Demo mode: anyone can act as anyone. Turn off WARROOM_DEMO_MODE before real people use this.
        </div>
      )}
      <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-bg/95 px-4 backdrop-blur md:px-6">
        <Link href="/" className="flex items-center gap-2.5" aria-label="Home">
          <span className="flex h-[22px] w-[22px] items-center justify-center rounded-[5px] border-2 border-accent">
            <span className="h-2 w-2 rounded-[2px] bg-accent" />
          </span>
          <span className="hidden text-[15px] font-semibold tracking-tight sm:inline">War Room OS</span>
        </Link>
        <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-2 text-sm text-muted">
          {crumbs.map((c, i) => (
            <span key={i} className="flex min-w-0 items-center gap-2">
              <span className="text-faint">/</span>
              {c.href ? <Link href={c.href} className="truncate hover:text-text">{c.label}</Link> : <span className="truncate text-text">{c.label}</span>}
            </span>
          ))}
        </nav>
        <div className="flex-1" />
        {actions}
        {me && (
          <button type="button" title={me.demo ? "Act as someone else" : "Sign out"}
            onClick={() => { if (me.demo) { setCurrentUser(null); router.push("/login"); } else void signOut(); }}
            className="group flex items-center gap-2 rounded-lg px-2 py-1 text-sm text-muted hover:bg-raised hover:text-text">
            {me.avatar
              ? <img src={me.avatar} alt="" width={26} height={26} className="h-[26px] w-[26px] rounded-full" />
              : <Initials name={me.name} size={26} />}
            <span className="hidden md:inline">{me.name}</span>
            <span className="hidden whitespace-nowrap text-xs text-faint group-hover:text-muted md:inline">{me.demo ? "Switch" : "Sign out"}</span>
          </button>
        )}
      </header>
      <main className={`mx-auto w-full px-4 py-8 md:px-6 ${wide ? "max-w-[1280px]" : "max-w-[1080px]"}`}>{children}</main>
    </div>
  );
}

export function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      <span className="text-text-2">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
  );
}

export const inputCls = "h-10 rounded-lg border border-line-strong bg-raised px-3 text-sm text-text placeholder:text-muted focus:border-accent focus:outline-none";

export const STAGE_LABEL: Record<string, string> = {
  plan: "Plan", build: "Build", staging: "Staging", go_no_go: "Go / no-go", live: "Live", closed: "Closed",
};

/** Where a release is, at a glance. Go / no-go is amber because that stage is waiting on a person. */
export const STAGE_TONE: Record<string, string> = {
  plan: "bg-info", build: "bg-agent", staging: "bg-info", go_no_go: "bg-accent", live: "bg-ok", closed: "bg-faint",
};
export const STAGE_TEXT: Record<string, string> = {
  plan: "text-info", build: "text-agent", staging: "text-info", go_no_go: "text-accent", live: "text-ok", closed: "text-muted",
};

/** The stage as a coloured dot plus its name, for lists where a whole bar is too much. */
export function StageDot({ stage }: { stage: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`h-1.5 w-1.5 rounded-full ${STAGE_TONE[stage] ?? "bg-faint"}`} />
      <span className={STAGE_TEXT[stage] ?? "text-muted"}>{STAGE_LABEL[stage] ?? stage}</span>
    </span>
  );
}

export function StageBar({ stages, current }: { stages: string[]; current: string }) {
  const idx = stages.indexOf(current);
  return (
    <span className="flex gap-1" aria-label={`Stage: ${STAGE_LABEL[current]}`}>
      {stages.map((s, i) => (
        <span key={s} className={`h-1.5 w-6 rounded-full transition-colors ${i < idx ? "bg-ok/45" : i === idx ? STAGE_TONE[s] ?? "bg-text" : "bg-line-strong"}`} />
      ))}
    </span>
  );
}
