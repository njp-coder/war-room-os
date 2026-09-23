"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ArrowSquareOut, Lock } from "@phosphor-icons/react";
import { post, usePoll } from "@/lib/api";
import { Field, Shell, inputCls } from "@/components/shell";
import { Button } from "@/components/ui";

type Home = { org: { name: string; kind: string }; clients: { id: string; name: string }[] };
type Repo = { full_name: string; private: boolean; language: string | null; description: string | null; pushed_at: string };
type Install = { id: number; account: string | null; kind: string; selection: string; repos: Repo[] };
type Picker = { mode: string; installations: Install[]; install_url?: string; name?: string };
type GhStatus = { connected: boolean; mode?: string; login?: string; install_url?: string; error?: string };

export default function NewProject() {
  const router = useRouter();
  const { data: h } = usePoll<Home>("/home", 60000);
  const { data: gh } = usePoll<GhStatus>("/github/status", 60000);
  const { data: picker, error: repoErr, reload: reloadRepos } = usePoll<Picker>(gh?.connected ? "/github/repos" : null, 120000);
  const repos = useMemo(() => (picker?.installations ?? []).flatMap((i) => i.repos), [picker]);
  const installUrl = picker?.install_url ?? gh?.install_url;
  const [name, setName] = useState("");
  const [client, setClient] = useState("");
  const [newClient, setNewClient] = useState("");
  const [repo, setRepo] = useState("");
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const shown = useMemo(() => repos.filter((r) => r.full_name.toLowerCase().includes(filter.toLowerCase())).slice(0, 8), [repos, filter]);
  const agency = h?.org.kind === "agency";

  const submit = async () => {
    setError(null);
    if (!name.trim()) return setError("Give the project a name.");
    if (!repo.trim()) return setError("Pick a repository or paste owner/name.");
    setBusy(true);
    try {
      let clientId: string | null = client || null;
      if (client === "__new") {
        if (!newClient.trim()) { setBusy(false); return setError("Name the new client."); }
        clientId = (await post("/clients", { name: newClient })).id;
      }
      const { id } = await post("/projects", { name, client: clientId });
      await post(`/projects/${id}/repos`, { full_name: repo });
      router.push(`/p/${id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setBusy(false);
    }
  };

  return (
    <Shell crumbs={[{ label: h?.org.name ?? "Org", href: "/" }, { label: "New project" }]}>
      <div className="mx-auto flex max-w-[620px] flex-col gap-8">
        <div className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">New project</h1>
          <p className="text-sm text-muted">Connect a repo and War Room OS builds the project&apos;s context: code structure, data lineage, endpoints, PR history and who knows what. No model tokens are spent on indexing.</p>
        </div>

        <Field label="Project name">
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="Checkout service" />
        </Field>

        {agency && (
          <Field label="Client" hint="Agencies group projects by client. Only people you add to this project will see it.">
            <select className={inputCls} value={client} onChange={(e) => setClient(e.target.value)}>
              <option value="">Internal (no client)</option>
              {h?.clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              <option value="__new">New client</option>
            </select>
            {client === "__new" && <input className={`${inputCls} mt-2`} value={newClient} onChange={(e) => setNewClient(e.target.value)} placeholder="Client name" />}
          </Field>
        )}

        <Field label="Repository"
          hint={gh?.mode === "app"
            ? "Only repos you've installed War Room on appear here. Install it on more and they show up straight away."
            : gh?.connected ? `Using GitHub as ${gh.login}.` : "GitHub isn't connected yet."}>
          <input className={inputCls} value={repo} onChange={(e) => { setRepo(e.target.value); setFilter(e.target.value); }} placeholder="owner/repo" />
          {gh?.connected && (
            <div className="mt-2 flex flex-col divide-y divide-line rounded-lg border border-line">
              {!repos && !repoErr && <div className="h-24 animate-pulse bg-panel" />}
              {repoErr && <p className="p-3 text-xs text-bad">{repoErr}</p>}
              {shown.map((r) => (
                <button key={r.full_name} type="button" onClick={() => { setRepo(r.full_name); if (!name) setName(r.full_name.split("/")[1]); }}
                  className={`flex items-center gap-3 px-3 py-2.5 text-left text-sm hover:bg-raised ${repo === r.full_name ? "bg-raised" : ""}`}>
                  <span className="min-w-0 flex-1 truncate">{r.full_name}</span>
                  {r.private && <Lock size={12} className="text-muted" aria-label="Private" />}
                  <span className="text-xs text-muted">{r.language ?? ""}</span>
                </button>
              ))}
              {picker && shown.length === 0 && (
                <p className="p-3 text-xs text-muted">
                  {repos.length === 0 ? "War Room isn't installed on any repo yet." : "No match."}
                </p>
              )}
            </div>
          )}
          {installUrl && (
            <a href={installUrl} target="_blank" rel="noreferrer" onClick={() => setTimeout(reloadRepos, 4000)}
              className="mt-2 inline-flex items-center gap-1.5 text-[13px] text-info hover:underline">
              {repos.length === 0 ? "Install War Room on GitHub" : "Add another repo or org on GitHub"} <ArrowSquareOut size={12} />
            </a>
          )}
          {picker?.mode === "app" && repos.length > 0 && (
            <p className="mt-1.5 text-[11px] text-muted">
              {picker.installations.map((i) => `${i.account} (${i.selection === "all" ? "all repos" : `${i.repos.length} repo${i.repos.length === 1 ? "" : "s"}`})`).join(", ")}
            </p>
          )}
        </Field>

        {error && <p className="text-sm text-bad">{error}</p>}
        <div className="flex gap-3">
          <Button variant="primary" disabled={busy} onClick={submit}>{busy ? "Connecting" : "Create and build context"}</Button>
          <Button variant="quiet" onClick={() => router.push("/")}>Cancel</Button>
        </div>
      </div>
    </Shell>
  );
}
