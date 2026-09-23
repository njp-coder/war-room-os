"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { GithubLogo, Warning } from "@phosphor-icons/react";
import { setCurrentUser } from "@/lib/api";
import { Initials } from "@/components/ui";

type U = { id: string; name: string; title: string; org_role: string };
type Cfg = { github: boolean; demo: boolean; gated: boolean };

export default function Login() {
  const router = useRouter();
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [users, setUsers] = useState<U[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [next, setNext] = useState("/");
  const [code, setCode] = useState("");

  useEffect(() => {
    const qs = new URLSearchParams(location.search);
    setError(qs.get("error"));
    setNext(qs.get("next") ?? "/");
    setCode(qs.get("code") ?? "");
    fetch("/api/auth/config").then((r) => r.json()).then((c: Cfg) => {
      setCfg(c);
      if (c.demo) fetch("/api/users").then((r) => r.json()).then(setUsers).catch(() => undefined);
    }).catch(() => setError("Can't reach the War Room API."));
  }, []);

  const pick = (id: string) => { setCurrentUser(id); router.push("/"); };
  const groups = users ? [
    { title: "Org admins", rows: users.filter((u) => u.org_role === "admin") },
    { title: "Team", rows: users.filter((u) => u.org_role === "member") },
    { title: "Client guests", rows: users.filter((u) => u.org_role === "guest") },
  ] : [];

  return (
    <div className="mx-auto flex min-h-[100dvh] max-w-[480px] flex-col justify-center gap-8 px-5 py-12">
      <div className="flex flex-col gap-3">
        <span className="flex h-[26px] w-[26px] items-center justify-center rounded-[6px] border-2 border-accent">
          <span className="h-2.5 w-2.5 rounded-[2px] bg-accent" />
        </span>
        <h1 className="text-3xl font-semibold tracking-tight">Sign in to War Room OS</h1>
        <p className="text-sm leading-relaxed text-muted">
          Sign in with the GitHub account you write and review code with. That&apos;s how War Room knows which changes are yours,
          what you&apos;ve reviewed and who should sign off.
        </p>
      </div>

      {error && <p role="alert" className="rounded-lg border border-[#4a2a2a] bg-[#1c1414] px-3 py-2 text-sm text-bad">{error}</p>}
      {!cfg && !error && <div className="h-11 animate-pulse rounded-lg bg-panel" />}

      {cfg?.gated && !code && (
        <p className="rounded-lg border border-line px-3.5 py-2.5 text-[13px] leading-relaxed text-text-2">
          War Room is invite-only right now. Sign in if your GitHub account was added, or use the invite link you were sent.
        </p>
      )}

      {cfg?.github && (
        <a href={`/api/auth/github/start?next=${encodeURIComponent(next)}${code ? `&code=${encodeURIComponent(code)}` : ""}`}
          className="inline-flex h-11 items-center justify-center gap-2.5 rounded-lg bg-text px-4 text-sm font-semibold text-bg transition hover:brightness-95 active:scale-[0.99]">
          <GithubLogo size={18} weight="fill" /> Continue with GitHub
        </a>
      )}

      {cfg && !cfg.github && (
        <div className="flex flex-col gap-2 rounded-xl border border-line px-4 py-3.5 text-[13px] leading-relaxed text-text-2">
          <span className="font-medium text-text">GitHub sign-in isn&apos;t set up on this server yet.</span>
          <span>Whoever runs it: create a GitHub OAuth App with the callback URL <code className="break-all text-text">{typeof window !== "undefined" ? location.origin : ""}/api/auth/github/callback</code>, then put its client ID and secret in <code className="text-text">.env</code> as GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET and restart the API.</span>
        </div>
      )}

      {cfg?.demo && (
        <section className="flex flex-col gap-3 border-t border-line pt-6">
          <div className="flex items-start gap-2 text-[13px] text-[#e8c88a]">
            <Warning size={16} className="mt-0.5 shrink-0" />
            <span>Demo mode is on. Pick anyone to see what their role sees. Anyone who can open this page can do the same.</span>
          </div>
          {groups.map((g) => g.rows.length > 0 && (
            <div key={g.title} className="flex flex-col gap-1">
              <h2 className="text-xs font-semibold text-muted">{g.title}</h2>
              {g.rows.map((u) => (
                <button key={u.id} type="button" onClick={() => pick(u.id)}
                  className="flex items-center gap-3 rounded-lg px-3 py-2 text-left hover:bg-raised">
                  <Initials name={u.name} size={30} />
                  <span className="flex-1 text-sm">{u.name}</span>
                  <span className="text-xs text-muted">{u.title}</span>
                </button>
              ))}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
