"use client";

import { useState } from "react";
import { del, post, usePoll } from "@/lib/api";
import { Shell, inputCls } from "@/components/shell";
import { Button, Initials } from "@/components/ui";

type P = { id: string; name: string; github: string | null; avatar: string | null; org_role: string; joined: number | null; title: string };
type D = { members: P[]; invited: P[]; suggested: { github: string; activity: number }[]; can_manage: boolean; me: string };

function Face({ p }: { p: P }) {
  return p.avatar
    ? <img src={p.avatar} alt="" width={30} height={30} className="h-[30px] w-[30px] rounded-full" />
    : <Initials name={p.name} size={30} dim={!p.joined} />;
}

/** Who is in the org. People join by signing in with the GitHub account they were invited as. */
export default function People() {
  const { data: d, reload } = usePoll<D>("/org/people", 15000);
  const [handle, setHandle] = useState("");
  const [role, setRole] = useState("member");
  const [err, setErr] = useState<string | null>(null);
  if (!d) return <Shell crumbs={[{ label: "People" }]}><div className="h-40 animate-pulse rounded-2xl bg-panel" /></Shell>;

  const act = async (f: () => Promise<unknown>) => {
    setErr(null);
    try { await f(); reload(); } catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
  };
  const invite = (login: string, r = role) => act(async () => { await post("/org/invites", { github: login, role: r }); setHandle(""); });

  return (
    <Shell crumbs={[{ label: "People" }]}>
      <div className="flex max-w-[820px] flex-col gap-10 md:px-10">
        <header className="flex flex-col gap-2">
          <h1 className="text-[30px] font-semibold tracking-tight">People</h1>
          <p className="max-w-[68ch] text-base text-[#b8b7b2]">
            {d.members.length} {d.members.length === 1 ? "person has" : "people have"} signed in
            {d.invited.length > 0 ? `, and ${d.invited.length} ${d.invited.length === 1 ? "invite is" : "invites are"} waiting for a first sign-in` : ""}.
            {" "}Invite by GitHub handle: they join the moment they sign in with that account.
          </p>
        </header>

        {d.can_manage && (
          <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); void invite(handle); }}>
            <div className="flex min-w-[220px] flex-1 flex-col gap-1.5">
              <label htmlFor="inv-handle" className="text-[13px] font-medium">GitHub handle</label>
              <input id="inv-handle" className={inputCls} value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="@their-handle" autoComplete="off" />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="inv-role" className="text-[13px] font-medium">Role in the org</label>
              <select id="inv-role" className={`${inputCls} w-40`} value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
            </div>
            <Button type="submit" variant="primary" disabled={handle.trim().replace("@", "").length < 1}>Invite</Button>
          </form>
        )}
        {err && <p role="alert" className="-mt-6 text-sm text-bad">{err}</p>}

        <section className="flex flex-col gap-3">
          <h2 className="text-[15px] font-semibold">Signed in</h2>
          <ul className="flex flex-col divide-y divide-line rounded-2xl border border-line">
            {d.members.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                <Face p={p} />
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-sm">{p.name}{p.id === d.me && <span className="text-muted"> (you)</span>}</span>
                  {p.github && <span className="text-xs text-muted">@{p.github}</span>}
                </span>
                {d.can_manage && p.id !== d.me ? (
                  <>
                    <label className="sr-only" htmlFor={`r-${p.id}`}>Role for {p.name}</label>
                    <select id={`r-${p.id}`} className={`${inputCls} h-8 w-28 text-[13px]`} value={p.org_role}
                      onChange={(e) => act(() => post(`/org/people/${p.id}/role`, { role: e.target.value }))}>
                      <option value="member">Member</option><option value="admin">Admin</option>
                    </select>
                    <button type="button" className="text-xs text-muted hover:text-bad"
                      onClick={() => confirm(`Remove ${p.name}? They lose access to every project in this org and are signed out now. What they did stays attributed to them.`) && act(() => del(`/org/people/${p.id}`))}>
                      Remove
                    </button>
                  </>
                ) : <span className="text-xs text-muted">{p.org_role === "admin" ? "Admin" : "Member"}</span>}
              </li>
            ))}
          </ul>
        </section>

        {d.invited.length > 0 && (
          <section className="flex flex-col gap-3">
            <h2 className="text-[15px] font-semibold">Invited, not signed in yet</h2>
            <ul className="flex flex-col divide-y divide-line rounded-2xl border border-line">
              {d.invited.map((p) => (
                <li key={p.id} className="flex items-center gap-3 px-5 py-2.5">
                  <Face p={p} />
                  <span className="flex-1 text-sm text-text-2">@{p.github}</span>
                  <span className="text-xs text-muted">{p.org_role === "admin" ? "Admin" : "Member"}</span>
                  {d.can_manage && <button type="button" className="text-xs text-muted hover:text-text" onClick={() => act(() => del(`/org/invites/${p.id}`))}>Cancel</button>}
                </li>
              ))}
            </ul>
            <p className="text-xs text-muted">Send them this site&apos;s address. There&apos;s no email: signing in with GitHub is the invite.</p>
          </section>
        )}

        {d.can_manage && d.suggested.length > 0 && (
          <section className="flex flex-col gap-3">
            <h2 className="text-[15px] font-semibold">Working in your repos, not invited</h2>
            <p className="-mt-1 text-[13px] text-muted">From commits, reviews and CODEOWNERS in your connected repos.</p>
            <div className="flex flex-wrap gap-2">
              {d.suggested.map((s) => (
                <button key={s.github} type="button" onClick={() => void invite(s.github, "member")}
                  className="inline-flex h-8 items-center gap-2 rounded-full border border-line px-3 text-[13px] text-text-2 hover:border-line-strong hover:text-text">
                  @{s.github}<span className="text-faint">Invite</span>
                </button>
              ))}
            </div>
          </section>
        )}
      </div>
    </Shell>
  );
}
