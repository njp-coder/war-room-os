"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { get, post, signOut } from "@/lib/api";
import { inputCls } from "@/components/shell";
import { Button } from "@/components/ui";

type Me = { name: string; github: string | null; org: string | null };
type Invite = { id: string; org_name: string; org_role: string; invited_by: string | null; invited_by_github: string | null; members: number };

/** First sign-in with no invite: make the org. Anyone invited skips this and lands in their org. */
export default function Welcome() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"company" | "agency">("company");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [invites, setInvites] = useState<Invite[]>([]);

  useEffect(() => {
    get("/me").then((m: Me) => (m.org ? router.replace("/") : setMe(m))).catch(() => router.replace("/login"));
    get("/org/invites/pending").then((r: { invites: Invite[] }) => setInvites(r.invites ?? [])).catch(() => undefined);
  }, [router]);

  const accept = async (id: string) => {
    setErr(null); setBusy(true);
    try { await post("/org/invites/accept", { invite: id }); router.replace("/"); }
    catch (x) { setErr(x instanceof Error ? x.message : String(x)); setBusy(false); }
  };

  if (!me) return <div className="mx-auto mt-40 h-40 max-w-[480px] animate-pulse rounded-2xl bg-panel" />;

  return (
    <div className="mx-auto flex min-h-[100dvh] max-w-[480px] flex-col justify-center gap-8 px-5 py-12">
      <div className="flex flex-col gap-2">
        <h1 className="text-3xl font-semibold tracking-tight">Welcome, {me.name.split(" ")[0]}</h1>
        <p className="text-sm leading-relaxed text-muted">
          {invites.length > 0
            ? `Someone has invited @${me.github} to join their org. Accept it only if you recognise who sent it: an org admin can see your name and email, and what you do in their projects.`
            : `Nobody has invited @${me.github} to an org yet, so start one. You'll be its admin and can invite your team by their GitHub handles.`}
        </p>
      </div>

      {invites.length > 0 && (
        <section className="flex flex-col gap-2">
          {invites.map((i) => (
            <div key={i.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-4 py-3">
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="text-sm font-medium">{i.org_name}</span>
                <span className="text-xs text-muted">
                  {i.invited_by_github ? `Invited by @${i.invited_by_github}` : "Invited"}, as {i.org_role === "admin" ? "an admin" : "a member"}
                  {i.members > 0 ? `, ${i.members} ${i.members === 1 ? "person" : "people"} inside` : ", nobody has joined it yet"}
                </span>
              </span>
              <Button variant="primary" size="sm" disabled={busy} onClick={() => accept(i.id)}>Accept</Button>
            </div>
          ))}
          <p className="text-xs text-muted">Don&apos;t recognise it? Ignore it and make your own org below.</p>
        </section>
      )}
      <form className="flex flex-col gap-5" onSubmit={async (e) => {
        e.preventDefault(); setErr(null); setBusy(true);
        try { await post("/orgs", { name, kind }); router.replace("/"); } catch (x) { setErr(x instanceof Error ? x.message : String(x)); setBusy(false); }
      }}>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="org-name" className="text-[13px] font-medium">Org name</label>
          <input id="org-name" className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="Your company or studio" maxLength={80} />
        </div>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1.5 text-[13px] font-medium">You build software</legend>
          {([["company", "For our own product", "Projects are grouped as one team's work."], ["agency", "For clients", "Projects are grouped by client, and clients can get a guest view."]] as const).map(([v, t, d]) => (
            <label key={v} className={`flex cursor-pointer gap-3 rounded-xl border px-4 py-3 ${kind === v ? "border-text-2 bg-raised" : "border-line hover:border-line-strong"}`}>
              <input type="radio" name="kind" value={v} checked={kind === v} onChange={() => setKind(v)} className="mt-1 accent-[var(--color-accent)]" />
              <span className="flex flex-col gap-0.5"><span className="text-sm">{t}</span><span className="text-xs text-muted">{d}</span></span>
            </label>
          ))}
        </fieldset>
        <div className="flex items-center gap-4">
          <Button type="submit" variant="primary" disabled={busy || name.trim().length < 2}>{busy ? "Creating" : "Create org"}</Button>
          <button type="button" onClick={() => void signOut()} className="text-[13px] text-muted hover:text-text">Use another GitHub account</button>
        </div>
        {err && <p role="alert" className="text-sm text-bad">{err}</p>}
      </form>
    </div>
  );
}
