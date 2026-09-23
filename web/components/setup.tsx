"use client";

import { useState } from "react";
import { ArrowsClockwise, Check, CircleNotch, Copy } from "@phosphor-icons/react";
import { del, get, post, usePoll } from "@/lib/api";
import { Field, Section, inputCls } from "./shell";
import { Button } from "./ui";

export type SetupItem = { id: string; done: boolean; title: string; detail: string; why: string };
export type SetupData = { sentence: string; items: SetupItem[]; role: string };
type Layer = { state: string; detail: string };
export type ProjectData = {
  project: { id: string; name: string; client_name: string | null; description: string; demo: number; staging_url: string | null; settings: { client_facing?: boolean } };
  role: string;
  repos: { full_name: string; status: string; progress: Record<string, Layer>; synced: number | null }[];
  members: { id: string; name: string; title: string; role: string; shift: string; hours_today: number }[];
  releases: { id: string; name: string; stage: string; window_start: string; open_bugs: number; blockers: number }[];
  org_users: { id: string; name: string; title: string }[];
};

const DONE: Record<string, string> = {
  repo: "Repo synced", people: "People added", staging_db: "Staging database connected", monitor_db: "Production monitoring connected",
  sources: "Logs and errors connected", codeowners: "CODEOWNERS found", staging_url: "Staging URL set", brief: "Agents know how the product is used",
};
const LAYERS = ["clone", "structure", "data", "service", "history", "people", "docs"];
const ROLES = ["owner", "lead", "engineer", "tester", "support", "viewer", "client_guest"];

/** Segments go green as things get connected. Nothing here is amber: setup never blocks today's work. */
export function SetupProgress({ items }: { items: SetupItem[] }) {
  return (
    <div className="flex gap-1.5" aria-label={`${items.filter((i) => i.done).length} of ${items.length} done`}>
      {items.map((i) => <span key={i.id} className={`h-1.5 flex-1 rounded-full ${i.done ? "bg-ok" : "bg-line-strong"}`} />)}
    </div>
  );
}

export function Checklist({ pid, s, lead, reload }: { pid: string; s: SetupData; lead: boolean; reload: () => void }) {
  const todo = s.items.filter((i) => !i.done);
  const done = s.items.filter((i) => i.done);
  return (
    <div className="flex flex-col gap-8">
      {todo.length > 0 && (
        <section className="flex flex-col gap-2.5">
          <h2 className="text-[13px] font-semibold text-text-2">To do</h2>
          <div className="flex flex-col divide-y divide-line rounded-2xl border border-line">
            {todo.map((i) => (
              <div key={i.id} className="grid gap-3 px-5 py-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] md:items-center md:gap-8">
                <div className="flex items-start gap-3">
                  <span className="mt-1 h-4 w-4 shrink-0 rounded-full border border-line-strong" />
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <span className="text-[15px] font-medium">{i.title}</span>
                    <span className="text-[13px] text-muted">{i.why}</span>
                  </div>
                </div>
                {lead ? <Inline pid={pid} item={i} reload={reload} /> : <span className="text-xs text-muted">A lead can set this up.</span>}
              </div>
            ))}
          </div>
        </section>
      )}
      {done.length > 0 && (
        <section className="flex flex-col gap-2.5">
          <h2 className="text-[13px] font-semibold text-text-2">Done</h2>
          <div className="flex flex-col divide-y divide-line rounded-2xl border border-line">
            {done.map((i) => (
              <div key={i.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                <Check size={16} className="shrink-0 text-ok" />
                <span className="text-sm">{DONE[i.id] ?? i.title}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-muted">{i.detail}</span>
                {lead && (i.id === "staging_db" || i.id === "monitor_db") && (
                  <button type="button" className="text-xs text-muted hover:text-text"
                    onClick={async () => { await del(i.id === "staging_db" ? `/projects/${pid}/staging_db` : `/projects/${pid}/monitor`); reload(); }}>Disconnect</button>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

/** The action for one to-do, right in its row. */
export function Inline({ pid, item, reload }: { pid: string; item: SetupItem; reload: () => void }) {
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); setVal(""); reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };
  const urlForm = (path: string, placeholder: string, secret = true) => (
    <form className="flex flex-col gap-1.5" onSubmit={(e) => { e.preventDefault(); run(() => post(path, secret ? { url: val } : { staging_url: val })); }}>
      <div className="flex gap-2">
        <label className="sr-only" htmlFor={`in-${item.id}`}>{item.title}</label>
        <input id={`in-${item.id}`} type={secret ? "password" : "url"} autoComplete="off" className={`${inputCls} min-w-0 flex-1`} value={val}
          onChange={(e) => setVal(e.target.value)} placeholder={placeholder} />
        <Button type="submit" disabled={busy || !val}>{busy ? "Checking" : secret ? "Connect" : "Save"}</Button>
      </div>
      {err ? <span className="text-xs text-bad">{err}</span> : secret && <span className="text-[11px] text-muted">Postgres, MySQL, SQL Server, Snowflake, BigQuery, MongoDB, DynamoDB or SQLite. Use a read-only user. Encrypted at rest, never shown again in full.</span>}
    </form>
  );
  if (item.id === "staging_db") return urlForm(`/projects/${pid}/staging_db`, "postgresql://readonly@staging-host/db");
  if (item.id === "monitor_db") return urlForm(`/projects/${pid}/monitor`, "postgresql://monitor_ro@prod-host/db");
  if (item.id === "staging_url") return urlForm(`/projects/${pid}/settings`, "https://staging.example.com", false);
  if (item.id === "sources") return <a href="#sources" className="text-sm text-text-2 underline-offset-2 hover:underline">Pick Sentry, Datadog, Loki or a log file below</a>;
  if (item.id === "brief") return <a href="#brief" className="text-sm text-text-2 underline-offset-2 hover:underline">{item.detail ? `Continue: ${item.detail}` : "Answer a few questions below"}</a>;
  if (item.id === "people") return <a href="#people" className="text-sm text-text-2 underline-offset-2 hover:underline">Add people who wrote this code</a>;
  if (item.id === "repo") return <span className="text-sm text-muted">{item.detail}</span>;
  if (item.id === "codeowners") return (
    <div className="flex flex-col gap-2">
      {!draft ? <Button className="self-start" disabled={busy} onClick={async () => { setBusy(true); setDraft((await get(`/projects/${pid}/codeowners/draft`)).text); setBusy(false); }}>
        {busy ? "Drafting" : "Draft one from PR history"}</Button> : (
        <>
          <pre className="max-h-48 overflow-auto rounded-lg border border-line bg-panel p-3 font-mono text-[11px] text-text-2">{draft}</pre>
          <div className="flex items-center gap-3">
            <Button size="sm" onClick={() => navigator.clipboard.writeText(draft)}><Copy size={13} />Copy</Button>
            <span className="text-[11px] text-muted">Review it, commit it as .github/CODEOWNERS, then sync the repo.</span>
          </div>
        </>
      )}
    </div>
  );
  return null;
}

type Mon = { connected: boolean; url?: string; settings: Record<string, number | string | boolean> };

const MODES = [
  { id: "suggest", label: "Suggest", hint: "The agent proposes, a person opens the war room" },
  { id: "auto_low", label: "Auto for medium", hint: "Opens medium on its own, proposes high" },
  { id: "auto", label: "Auto", hint: "Opens everything that crosses the guardrails" },
];

/** When the monitoring agent may open a war room by itself. */
export function Guardrails({ pid, hasSources }: { pid: string; hasSources: boolean }) {
  const { data: mon, reload } = usePoll<Mon>(`/projects/${pid}/monitor`, 30000);
  if (!mon || (!mon.connected && !hasSources)) return null;
  const s = mon.settings;
  const save = async (patch: Record<string, unknown>) => { await post(`/projects/${pid}/monitor/settings`, { settings: patch }); reload(); };
  const num = (k: string, label: string, unit: string, min: number, max: number, step = 1) => (
    <label className="flex flex-col gap-1.5 text-xs text-text-2">
      <span className="flex justify-between"><span>{label}</span><span className="font-mono text-text">{String(s[k])}{unit}</span></span>
      <input type="range" min={min} max={max} step={step} defaultValue={Number(s[k])} disabled={!s.enabled}
        onMouseUp={(e) => save({ [k]: Number((e.target as HTMLInputElement).value) })} onKeyUp={(e) => save({ [k]: Number((e.target as HTMLInputElement).value) })} />
    </label>
  );
  return (
    <Section title="Automatic monitoring" action={
      <button type="button" role="switch" aria-checked={!!s.enabled} onClick={() => save({ enabled: !s.enabled })}
        className="flex items-center gap-2 text-[13px] text-text-2">
        <span className={`relative h-5 w-9 rounded-full transition-colors ${s.enabled ? "bg-ok" : "bg-line-strong"}`}>
          <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-text transition-[left] ${s.enabled ? "left-[18px]" : "left-0.5"}`} />
        </span>
        {s.enabled ? "On" : "Off"}
      </button>}>
      <p className="-mt-2 text-[13px] text-muted">{s.enabled
        ? `Checks production every ${s.interval_s}s against these guardrails. It never kills a query, adds an index or rolls back: it proposes, a person approves.`
        : "Off. Nothing is read from production and no war room is opened automatically. People can still open one by hand."}</p>
      <div className={`flex flex-col gap-5 rounded-2xl border border-line p-5 ${s.enabled ? "" : "opacity-50"}`}>
        <div className="grid gap-2 sm:grid-cols-3">
          {MODES.map((m) => (
            <button key={m.id} type="button" disabled={!s.enabled} onClick={() => save({ mode: m.id })}
              className={`flex flex-col gap-1 rounded-lg border p-3 text-left ${s.mode === m.id ? "border-text-2 bg-raised" : "border-line hover:bg-raised"}`}>
              <span className="text-sm">{m.label}</span><span className="text-[11px] text-muted">{m.hint}</span>
            </button>
          ))}
        </div>
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {mon.connected && <>
            {num("slow_ms", "Slow query above", " ms", 10, 3000, 10)}
            {num("slow_factor", "And slower than baseline by", "x", 1.5, 10, 0.5)}
            {num("spike_factor", "Or a spike of", "x baseline", 3, 100, 1)}
            {num("long_running_s", "Long-running query above", " s", 5, 600, 5)}
            {num("blocked_sessions", "Sessions waiting on locks", "", 1, 50)}
            {num("rollbacks_per_min", "Failed transactions per minute", "", 1, 1000, 1)}
          </>}
          {hasSources && <>
            {num("errors_min", "Error volume above", " / check", 1, 500)}
            {num("new_error_min", "A new error seen", " times", 1, 200)}
          </>}
          {num("sustain", "Must last", " checks", 1, 10)}
          {num("cooldown_min", "Quiet after a war room", " min", 5, 240, 5)}
          {num("max_auto_per_hour", "At most", " auto war rooms / hour", 0, 10)}
        </div>
      </div>
    </Section>
  );
}

type Source = { id: string; kind: string; name: string; config: Record<string, string>; status: string; last_polled: number | null; patterns: number };
type SourcesData = { sources: Source[]; kinds: Record<string, { label: string; fields: string[]; secret: string | null; help: string }>;
  patterns: { fingerprint: string; template: string; total: number; last_seen: number; source: string }[] };

/** Where errors and logs come from. Stack traces map straight to code and PRs. */
export function Sources({ pid, lead, onChange }: { pid: string; lead: boolean; onChange: () => void }) {
  const { data, reload } = usePoll<SourcesData>(`/projects/${pid}/sources`, 20000);
  const [kind, setKind] = useState<string | null>(null);
  const [cfg, setCfg] = useState<Record<string, string>>({});
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!data) return null;
  const k = kind ? data.kinds[kind] : null;
  const add = async () => {
    setBusy(true); setErr(null);
    try {
      await post(`/projects/${pid}/sources`, { kind, name: cfg.name || k?.label, config: cfg, secret: secret || null });
      setKind(null); setCfg({}); setSecret(""); reload(); onChange();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };
  return (
    <section id="sources" className="flex scroll-mt-20 flex-col gap-4">
      <h2 className="text-sm font-semibold">Logs and errors</h2>
      {data.sources.length > 0 && (
        <div className="flex flex-col divide-y divide-line rounded-2xl border border-line">
          {data.sources.map((s) => (
            <div key={s.id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
              {s.status?.startsWith("error") ? <span className="h-2 w-2 rounded-full bg-bad" /> : <Check size={14} className="text-ok" />}
              <span>{s.name}</span>
              <span className="text-xs text-muted">{data.kinds[s.kind]?.label}</span>
              <span className="min-w-0 flex-1 truncate text-xs text-muted">{s.status?.startsWith("error") ? s.status : s.patterns ? `${s.patterns} error patterns` : ""}</span>
              {lead && <button type="button" className="text-xs text-muted hover:text-text" onClick={async () => { await del(`/projects/${pid}/sources/${s.id}`); reload(); onChange(); }}>Remove</button>}
            </div>
          ))}
        </div>
      )}
      {lead && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-2">
            {Object.entries(data.kinds).map(([id, v]) => (
              <button key={id} type="button" onClick={() => { setKind(kind === id ? null : id); setCfg({}); setErr(null); }}
                className={`h-9 rounded-lg border px-3 text-sm ${kind === id ? "border-text-2 bg-raised" : "border-line-strong text-text-2 hover:bg-raised"}`}>+ {v.label}</button>
            ))}
          </div>
          {k && (
            <form className="enter grid gap-4 rounded-2xl border border-line bg-panel p-5 md:grid-cols-2" onSubmit={(e) => { e.preventDefault(); add(); }}>
              <p className="text-[13px] text-muted md:col-span-2">{k.help}</p>
              {k.fields.map((f) => (
                <Field key={f} label={f.replace("_", " ")}>
                  <input className={inputCls} value={cfg[f] ?? ""} onChange={(e) => setCfg({ ...cfg, [f]: e.target.value })}
                    placeholder={{ path: "/var/log/app/api.log", org: "acme", base_url: "https://sentry.io", site: "datadoghq.com",
                      query: kind === "loki" ? '{app="api"} |= "error"' : "service:api status:error", url: "https://grafana.example.com/api/datasources/proxy/uid/loki",
                      project: kind === "vercel" ? "my-api" : "backend", team: "your-team (leave empty for a personal account)", environment: "production" }[f]} />
                </Field>
              ))}
              {k.secret && (
                <Field label={k.secret}>
                  <input type="password" autoComplete="off" className={inputCls} value={secret} onChange={(e) => setSecret(e.target.value)} />
                </Field>
              )}
              <div className="flex items-start gap-3 md:col-span-2 md:justify-end">
                {err && <span role="alert" className="min-w-0 flex-1 break-words pt-2 text-xs leading-relaxed text-bad">{err.replace(/^error:\s*/, "")}</span>}
                <Button type="submit" variant="primary" disabled={busy} className="shrink-0">{busy ? "Checking" : "Connect"}</Button>
              </div>
            </form>
          )}
        </div>
      )}
      {data.patterns.length > 0 && (
        <details className="rounded-2xl border border-line px-5 py-3">
          <summary className="cursor-pointer text-[13px] text-text-2">{data.patterns.length} error patterns seen</summary>
          <ul className="mt-3 flex flex-col gap-2">
            {data.patterns.map((p) => (
              <li key={p.fingerprint} className="flex gap-3 text-xs"><span className="w-12 shrink-0 text-right font-mono text-muted">{p.total}</span><span className="break-all font-mono text-text-2">{p.template}</span></li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

export function People({ d, pid, lead, reload }: { d: ProjectData; pid: string; lead: boolean; reload: () => void }) {
  const { data: cands, reload: reloadCands } = usePoll<{ login: string; wrote: number; reviewed: number; owns: number }[]>(lead ? `/projects/${pid}/team/candidates` : null, 60000);
  const [picked, setPicked] = useState<string[]>([]);
  const [user, setUser] = useState("");
  const [role, setRole] = useState("engineer");
  const available = d.org_users.filter((u) => !d.members.some((m) => m.id === u.id));
  const byRole = ROLES.map((r) => ({ r, people: d.members.filter((m) => m.role === r) })).filter((g) => g.people.length);
  return (
    <section id="people" className="flex scroll-mt-20 flex-col gap-4">
      <h2 className="text-sm font-semibold">People and access</h2>
      <div className="flex flex-col divide-y divide-line rounded-2xl border border-line">
        {byRole.map((g) => (
          <div key={g.r} className="grid gap-2 px-5 py-3 md:grid-cols-[120px_minmax(0,1fr)]">
            <span className="text-xs capitalize text-muted">{g.r.replace("_", " ")}s <span className="font-mono">{g.people.length}</span></span>
            <span className="text-sm">{g.people.map((m) => m.name).join(", ")}</span>
          </div>
        ))}
      </div>
      {lead && cands && cands.length > 0 && (
        <div className="flex flex-col gap-3 rounded-2xl border border-dashed border-line-strong p-4">
          <p className="text-sm">{cands.length} people work on this code but aren&apos;t here yet</p>
          <p className="text-xs text-muted">From PR authors, reviewers and CODEOWNERS. The dispatcher can only route fixes to people on the project.</p>
          <div className="flex flex-wrap gap-2">
            {cands.slice(0, 16).map((c) => {
              const on = picked.includes(c.login);
              return (
                <button key={c.login} type="button" onClick={() => setPicked(on ? picked.filter((x) => x !== c.login) : [...picked, c.login])} aria-pressed={on}
                  className={`flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${on ? "border-text-2 bg-raised text-text" : "border-line-strong text-text-2 hover:bg-raised"}`}>
                  {on && <Check size={11} />}{c.login}<span className="font-mono text-[10px] text-muted">{c.wrote} PRs</span>
                </button>
              );
            })}
          </div>
          <Button size="sm" className="self-start" disabled={!picked.length} onClick={async () => {
            await post(`/projects/${pid}/team/import`, { logins: picked, role: "engineer" }); setPicked([]); reload(); reloadCands();
          }}>{picked.length ? `Add ${picked.length} as engineers` : "Pick people to add"}</Button>
        </div>
      )}
      {lead && available.length > 0 && (
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Add someone from your org">
            <select className={`${inputCls} w-64`} value={user} onChange={(e) => setUser(e.target.value)}>
              <option value="">Pick a person</option>
              {available.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
          </Field>
          <Field label="Role">
            <select className={`${inputCls} w-40`} value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLES.map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}
            </select>
          </Field>
          <Button disabled={!user} onClick={async () => { await post(`/projects/${pid}/members`, { user, role }); setUser(""); reload(); }}>Add</Button>
        </div>
      )}
    </section>
  );
}

export function Repo({ d, pid, lead }: { d: ProjectData; pid: string; lead: boolean }) {
  if (!d.repos.length) return null;
  return (
    <Section title="Repository">
      {d.repos.map((r) => {
        const running = LAYERS.filter((l) => r.progress[l]?.state === "running");
        return (
          <div key={r.full_name} className="flex flex-col gap-3 rounded-2xl border border-line p-5">
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-medium">{r.full_name}</span>
              <span className="flex items-center gap-2 text-xs text-muted">
                {r.status === "syncing" ? <><CircleNotch size={14} className="animate-spin" /> Reading {running[0] ?? "the repo"}</> :
                  r.status === "error" ? <span className="text-bad">Sync failed</span> : r.synced ? `Synced ${new Date(r.synced * 1000).toLocaleString()}` : "Ready"}
                {lead && !d.project.demo && r.status !== "syncing" && (
                  <button type="button" aria-label="Sync again" className="rounded p-1 hover:bg-raised" onClick={() => post(`/projects/${pid}/repos/sync`, { full_name: r.full_name })}>
                    <ArrowsClockwise size={14} />
                  </button>
                )}
              </span>
            </div>
            {r.status === "syncing" && (
              <ol className="grid gap-2 sm:grid-cols-2">
                {LAYERS.map((l) => {
                  const st = r.progress[l];
                  return (
                    <li key={l} className="flex items-start gap-2 text-sm">
                      {st?.state === "done" ? <Check size={15} className="mt-0.5 shrink-0 text-ok" /> :
                        st?.state === "running" ? <CircleNotch size={15} className="mt-0.5 shrink-0 animate-spin" /> :
                          <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-line-strong" />}
                      <span className="min-w-0"><span className="capitalize">{l}</span>{st?.detail && <span className="block text-xs text-muted">{st.detail}</span>}</span>
                    </li>
                  );
                })}
              </ol>
            )}
            {r.progress.error && <p className="text-xs text-bad">{r.progress.error.detail}</p>}
          </div>
        );
      })}
    </Section>
  );
}

export function Settings({ d, pid, reload }: { d: ProjectData; pid: string; reload: () => void }) {
  return (
    <Section title="Settings">
      <label className="flex items-center gap-2 text-sm text-text-2">
        <input type="checkbox" checked={!!d.project.settings.client_facing}
          onChange={async (e) => { await post(`/projects/${pid}/settings`, { client_facing: e.target.checked }); reload(); }} />
        Client-facing product. Releases need an approved client update before go-live.
      </label>
      <label className="flex items-center gap-2 text-sm text-text-2">
        <input type="checkbox" checked={!!(d.project.settings as { explorer_writes?: boolean }).explorer_writes}
          onChange={async (e) => { await post(`/projects/${pid}/settings`, { explorer_writes: e.target.checked }); reload(); }} />
        Let the staging explorer send writes (POST, PUT, DELETE) to staging. Never production. Off by default.
      </label>
    </Section>
  );
}

type Expert = { id: string; name: string; paths: string[]; built: number; lessons: Record<string, number>;
  teachers: { login: string; n: number; member: boolean; name: string }[]; track: Record<string, number>;
  recent: { id: string; text: string; taught_by: string; source: string; ref: string }[] };
type ExpertsData = { experts: Expert[]; levels: { id: string; name: string; role: string; level: string | null }[]; choices: string[] };

const SOURCE_WORD: Record<string, string> = {
  review: "review comments", author_note: "authors' own notes", pr: "reasons behind changes", code: "rules written in the code",
  doc: "doc sections", test: "tested rules", brief: "answers from the brief", fix: "fixes that held", incident: "war rooms",
  manual: "taught directly", thread: "thread answers",
};

/**
 * Agents that stand in for the seniors of one area. Each shows exactly what it knows, who it learned it from,
 * and whether its advice has held up, so "expert" is something you can check.
 */
export function Experts({ pid, lead, canTeach }: { pid: string; lead: boolean; canTeach: boolean }) {
  const { data, reload } = usePoll<ExpertsData>(`/projects/${pid}/experts`, 30000);
  const { data: team } = usePoll<{ experts: { topic: string; name: string; lessons: number }[] }>(`/projects/${pid}/org-experts`, 60000);
  const [shared, setShared] = useState<Record<string, string>>({});
  const share = async (lid: string, topic: string) => {
    try { await post(`/lessons/${lid}/promote`, { topic }); setShared({ ...shared, [lid]: "Sent to an org admin to approve" }); }
    catch (e) { setShared({ ...shared, [lid]: e instanceof Error ? e.message : "Couldn't share" }); }
  };
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (!data) return null;
  const build = async () => { setBusy(true); try { await post(`/projects/${pid}/experts/build`); reload(); } finally { setBusy(false); } };
  const teach = async (eid: string) => {
    setErr(null);
    try { await post(`/experts/${eid}/teach`, { text }); setText(""); reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <section id="experts" className="flex scroll-mt-20 flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">Expert agents</h2>
        {lead && <Button size="sm" disabled={busy} onClick={build}>{busy ? "Reading history" : data.experts.length ? "Rebuild from history" : "Build from history"}</Button>}
      </div>
      <p className="-mt-2 max-w-[75ch] text-[13px] text-muted">
        One per area of the context map. Each learns from everything the project has written down: reviews and authors' notes, why each change was made,
        rules in code comments, docs, tests, your brief answers, fixes that held and war rooms closed.
        When the person fixing something isn&apos;t senior in that area, the expert reviews the plan in the thread. It never approves: risky changes still need a senior to sign off.
      </p>
      {data.experts.length === 0 && <p className="rounded-2xl border border-dashed border-line-strong px-5 py-6 text-sm text-muted">No areas yet. Sync the repo, then build.</p>}
      {team && team.experts.length > 0 && (
        <p className="text-[13px] text-muted">
          Also advising, from your <a href="/team" className="text-text-2 underline-offset-2 hover:underline">team knowledge</a>:{" "}
          {team.experts.map((t, i) => <span key={t.topic}>{i > 0 && ", "}{t.name.replace(" expert", "")}{t.lessons ? ` (${t.lessons})` : ""}</span>)}.
          {team.experts.every((t) => !t.lessons) && " They know nothing yet: upload a handbook or share proven lessons."}
        </p>
      )}
      <div className="grid gap-3.5 md:grid-cols-2">
        {data.experts.map((e) => {
          const total = Object.values(e.lessons).reduce((a, b) => a + b, 0);
          const advised = Object.values(e.track).reduce((a, b) => a + b, 0);
          const held = e.track.held ?? 0, followed = (e.track.followed ?? 0) + held;
          return (
            <div key={e.id} className="flex flex-col gap-3 rounded-2xl border border-line p-5">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[15px] font-medium">{e.name}</span>
                <span className="shrink-0 text-xs text-muted">{e.paths.length} file{e.paths.length === 1 ? "" : "s"}</span>
              </div>
              <p className="text-[13px] text-text-2">
                {total ? <>Knows {total} thing{total > 1 ? "s" : ""}: {Object.entries(e.lessons).map(([k, n]) => `${n} ${SOURCE_WORD[k] ?? k}`).join(", ")}.</> : "Knows nothing yet. Teach it, or let seniors review in this area."}
              </p>
              {e.teachers.length > 0 && (
                <p className="text-[13px] text-muted">Learned from {e.teachers.map((t, i) => (
                  <span key={t.login}>{i > 0 && ", "}<span className={t.member ? "text-text-2" : ""}>{t.name}</span>{!t.member && " (not on this project)"}</span>
                ))}</p>
              )}
              {advised > 0 && <p className="text-[13px] text-muted">Advised {advised} time{advised > 1 ? "s" : ""}{followed ? `, followed ${followed}` : ""}{held ? `, ${held} fix${held > 1 ? "es" : ""} held` : ""}{e.track.not_useful ? `, ${e.track.not_useful} not useful` : ""}.</p>}
              <button type="button" onClick={() => setOpen(open === e.id ? null : e.id)} className="self-start text-[13px] text-text-2 underline-offset-2 hover:underline">
                {open === e.id ? "Hide" : total ? "What it knows" : "Teach it"}
              </button>
              {open === e.id && (
                <div className="enter flex flex-col gap-3 border-t border-line pt-3">
                  {e.recent.map((l) => (
                    <div key={l.id} className="flex flex-col gap-1">
                      <p className="text-xs leading-relaxed text-text-2"><span className="text-muted">{l.taught_by}, {SOURCE_WORD[l.source] ?? l.source}: </span>{l.text.slice(0, 220)}{l.text.length > 220 ? "…" : ""}</p>
                      {lead && team && team.experts.length > 0 && (shared[l.id] ? <span className="text-[11px] text-muted">{shared[l.id]}</span> : (
                        <span className="flex items-center gap-1.5 text-[11px] text-muted">Share with the team as
                          <select aria-label="Topic" className="rounded border border-line-strong bg-raised px-1 py-0.5 text-[11px]" defaultValue=""
                            onChange={(ev) => ev.target.value && share(l.id, ev.target.value)}>
                            <option value="">pick a topic</option>
                            {team.experts.map((t) => <option key={t.topic} value={t.topic}>{t.name.replace(" expert", "")}</option>)}
                            <option value="general">General</option>
                          </select>
                        </span>
                      ))}
                    </div>
                  ))}
                  {canTeach && (
                    <form className="flex flex-col gap-2" onSubmit={(ev) => { ev.preventDefault(); teach(e.id); }}>
                      <label htmlFor={`teach-${e.id}`} className="text-xs text-muted">Teach it something a senior would say</label>
                      <textarea id={`teach-${e.id}`} rows={2} value={text} onChange={(ev) => setText(ev.target.value)}
                        placeholder="Never backfill item.title in the same migration that makes it required. Ship the backfill first."
                        className="rounded-lg border border-line-strong bg-raised px-3 py-2 text-sm placeholder:text-muted focus:border-text-2 focus:outline-none" />
                      <div className="flex items-center gap-3"><Button size="sm" type="submit" disabled={text.trim().length < 15}>Teach</Button>{err && <span className="text-xs text-bad">{err}</span>}</div>
                    </form>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {lead && data.levels.length > 0 && <Levels pid={pid} data={data} reload={reload} />}
    </section>
  );
}

/** Seniority is set by a lead. If it's blank, experience in the area (PRs written or reviewed) decides. */
function Levels({ pid, data, reload }: { pid: string; data: ExpertsData; reload: () => void }) {
  return (
    <details className="rounded-2xl border border-line px-5 py-3">
      <summary className="cursor-pointer text-[13px] text-text-2">Who counts as senior here ({data.levels.filter((l) => l.level === "senior" || l.level === "staff").length} marked)</summary>
      <p className="mt-2 text-xs text-muted">Leave it blank to let history decide: 5 or more PRs written or reviewed in an area makes someone senior there.</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {data.levels.map((m) => (
          <label key={m.id} className="flex items-center justify-between gap-3 text-sm">
            <span className="truncate">{m.name}</span>
            <select className={`${inputCls} h-8 w-32 text-xs`} value={m.level ?? ""} onChange={async (e) => { await post(`/projects/${pid}/members/${m.id}/level`, { level: e.target.value || null }); reload(); }}>
              <option value="">from history</option>
              {data.choices.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
        ))}
      </div>
    </details>
  );
}


export type BriefQ = { qid: string; kind: string; question: string; hint: string; choices: string[]; answer: string | null; answered_by: string | null };

/** What the code can't tell agents. Answers save on blur and become context agents search and reason with. */
export function Brief({ pid }: { pid: string }) {
  const { data, reload } = usePoll<{ questions: BriefQ[]; answered: number; can_answer: boolean }>(`/projects/${pid}/brief`, 60000);
  if (!data) return null;
  const core = data.questions.filter((q) => q.kind === "core");
  const gen = data.questions.filter((q) => q.kind !== "core");
  return (
    <section id="brief" className="flex scroll-mt-20 flex-col gap-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold">How the product is used</h2>
        <span className="text-xs text-muted">{data.answered} of {data.questions.length} answered</span>
      </div>
      <p className="-mt-2 max-w-[75ch] text-[13px] text-muted">
        Code shows what exists, not what matters. These answers tell agents which flows are critical, when peak is and what counts as broken.
        They use them when deciding if a signal is a real incident and when explaining a bug.
      </p>
      <div className="flex flex-col divide-y divide-line rounded-2xl border border-line">
        {core.map((q) => <Answer key={q.qid} url={`/projects/${pid}/brief/${q.qid}`} q={q} can={data.can_answer} onSaved={reload} />)}
      </div>
      {gen.length > 0 && (
        <>
          <h3 className="mt-2 text-[13px] font-semibold text-text-2">From your code</h3>
          <div className="flex flex-col divide-y divide-line rounded-2xl border border-line">
            {gen.map((q) => <Answer key={q.qid} url={`/projects/${pid}/brief/${q.qid}`} q={q} can={data.can_answer} onSaved={reload} />)}
          </div>
        </>
      )}
    </section>
  );
}

export function Answer({ url, q, can, onSaved }: { url: string; q: BriefQ; can: boolean; onSaved: () => void }) {
  const [text, setText] = useState(q.answer ?? "");
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const picked = new Set((q.answer ?? "").split(",").map((x) => x.trim()).filter(Boolean));
  const save = async (value: string) => {
    if (value.trim() === (q.answer ?? "").trim()) return;
    setState("saving");
    await post(url, { answer: value });
    setState("saved");
    onSaved();
    setTimeout(() => setState("idle"), 1500);
  };
  return (
    <div className="grid gap-3 px-5 py-4 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] md:gap-8">
      <div className="flex items-start gap-3">
        {q.answer ? <Check size={16} className="mt-0.5 shrink-0 text-ok" /> : <span className="mt-1 h-4 w-4 shrink-0 rounded-full border border-line-strong" />}
        <div className="flex min-w-0 flex-col gap-0.5">
          <label htmlFor={`b-${q.qid}`} className="text-[14px] font-medium leading-snug">{q.question}</label>
          {q.answered_by && <span className="text-[11px] text-muted">Answered by {q.answered_by}</span>}
        </div>
      </div>
      {q.choices.length ? (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label={q.question}>
          {q.choices.map((c) => {
            const on = picked.has(c);
            return (
              <button key={c} type="button" disabled={!can} aria-pressed={on}
                onClick={() => { const next = new Set(picked); if (on) next.delete(c); else next.add(c); save([...next].join(", ")); }}
                className={`rounded-md border px-2 py-1 font-mono text-[11px] ${on ? "border-text-2 bg-raised text-text" : "border-line-strong text-muted hover:text-text"}`}>
                {on && <Check size={10} className="mr-1 inline" />}{c}
              </button>
            );
          })}
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          <textarea id={`b-${q.qid}`} rows={2} disabled={!can} value={text} onChange={(e) => setText(e.target.value)} onBlur={() => save(text)}
            placeholder={q.hint} className="rounded-lg border border-line-strong bg-raised px-3 py-2 text-sm leading-relaxed placeholder:text-faint focus:border-text-2 focus:outline-none" />
          <span className="h-3 text-[11px] text-muted">{state === "saving" ? "Saving" : state === "saved" ? "Saved. Agents will use this." : ""}</span>
        </div>
      )}
    </div>
  );
}

type TrackRow = { id: string; name: string; kind: string; about: string; members: { id: string; name: string }[]; areas: string[];
  notes: number; answered: number; questions: number };
type TracksData = { tracks: TrackRow[]; mine: string[]; can_manage: boolean; areas: { id: string; name: string }[]; members: { id: string; name: string }[] };

/** Teams inside the project. Each owns areas of the context map and keeps its own context next to the shared one. */
export function Tracks({ pid }: { pid: string }) {
  const { data, reload } = usePoll<TracksData>(`/projects/${pid}/tracks`, 30000);
  const [f, setF] = useState({ name: "", kind: "business", about: "" });
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  if (!data) return null;
  const owner = (area: string) => data.tracks.find((t) => t.areas.includes(area));
  const patch = async (tid: string, body: Record<string, unknown>) => { await post(`/tracks/${tid}`, body); reload(); };
  return (
    <section id="tracks" className="flex scroll-mt-20 flex-col gap-4">
      <h2 className="text-sm font-semibold">Tracks</h2>
      <p className="-mt-2 max-w-[78ch] text-[13px] text-muted">
        Teams inside this project, like Ops, Sales or Platform. Each owns areas of the context map: problems in those areas go to that team,
        and releases that change them say so. Everyone shares the code context; each team adds what only it knows.
      </p>
      {data.tracks.length > 0 && (
        <div className="flex flex-col divide-y divide-line rounded-2xl border border-line">
          {data.tracks.map((t) => (
            <div key={t.id} className="flex flex-col gap-3 px-5 py-4">
              <div className="flex flex-wrap items-baseline gap-3">
                <a href={`/p/${pid}/tracks/${t.id}`} className="text-[15px] font-medium hover:underline">{t.name}</a>
                <span className="text-xs text-muted">{t.kind === "business" ? "Business team" : "Engineering team"}{t.about ? `: ${t.about}` : ""}</span>
                <span className="ml-auto text-xs text-muted">
                  {t.areas.length ? `Owns ${t.areas.map((a) => data.areas.find((x) => x.id === a)?.name ?? a).join(", ")}` : "Owns no areas yet"}
                  {t.answered ? `, ${t.answered} of ${t.questions} answered` : ""}
                </span>
                {data.can_manage && <button type="button" className="text-xs text-text-2 hover:underline" onClick={() => setOpen(open === t.id ? null : t.id)}>{open === t.id ? "Done" : "Edit"}</button>}
              </div>
              {open === t.id && (
                <div className="enter flex flex-col gap-3">
                  <div className="flex flex-col gap-1.5">
                    <span className="text-xs text-muted">Areas this team owns (one owner per area)</span>
                    <div className="flex flex-wrap gap-1.5">
                      {data.areas.map((a) => {
                        const on = t.areas.includes(a.id), other = owner(a.id);
                        return (
                          <button key={a.id} type="button" aria-pressed={on}
                            onClick={() => patch(t.id, { areas: on ? t.areas.filter((x) => x !== a.id) : [...t.areas, a.id] })}
                            className={`rounded-md border px-2 py-1 text-xs ${on ? "border-text-2 bg-raised text-text" : "border-line-strong text-muted hover:text-text"}`}>
                            {on && <Check size={10} className="mr-1 inline" />}{a.name}{!on && other ? ` (${other.name})` : ""}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <span className="text-xs text-muted">People in this team</span>
                    <div className="flex flex-wrap gap-1.5">
                      {data.members.map((m) => {
                        const on = t.members.some((x) => x.id === m.id);
                        return (
                          <button key={m.id} type="button" aria-pressed={on}
                            onClick={() => patch(t.id, { members: on ? t.members.filter((x) => x.id !== m.id).map((x) => x.id) : [...t.members.map((x) => x.id), m.id] })}
                            className={`rounded-full border px-2.5 py-1 text-xs ${on ? "border-text-2 bg-raised text-text" : "border-line-strong text-muted hover:text-text"}`}>
                            {on && <Check size={10} className="mr-1 inline" />}{m.name}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                  <button type="button" className="self-start text-xs text-muted hover:text-bad"
                    onClick={async () => { if (confirm(`Delete the ${t.name} track and its notes? Shared context stays.`)) { await del(`/tracks/${t.id}`); reload(); } }}>Delete track</button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {data.can_manage && (
        <form className="flex flex-wrap items-end gap-2" onSubmit={async (e) => {
          e.preventDefault(); setErr(null);
          try { const { id } = await post(`/projects/${pid}/tracks`, f); setF({ name: "", kind: f.kind, about: "" }); setOpen(id); reload(); }
          catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
        }}>
          <Field label="New track"><input className={`${inputCls} w-44`} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Ops" /></Field>
          <Field label="Kind">
            <select className={`${inputCls} w-44`} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
              <option value="business">Business (sales, ops, support)</option><option value="engineering">Engineering</option>
            </select>
          </Field>
          <Field label="What it looks after"><input className={`${inputCls} w-64`} value={f.about} onChange={(e) => setF({ ...f, about: e.target.value })} placeholder="Kitchens, riders, deliveries" /></Field>
          <Button type="submit" disabled={!f.name.trim()}>Add track</Button>
          {err && <span className="text-xs text-bad">{err}</span>}
        </form>
      )}
    </section>
  );
}
