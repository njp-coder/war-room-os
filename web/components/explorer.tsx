"use client";

import Link from "next/link";
import { useState } from "react";
import { Play } from "@phosphor-icons/react";
import { post, usePoll } from "@/lib/api";
import { Button } from "./ui";

type Result = { label: string; method: string; status: number | string; ms?: number; reads?: string[]; writes?: string[]; queries?: number;
  note?: string; only_observed?: string[]; only_inferred?: string[] };
type X = { run: { status: string; started: number; ended: number | null; summary: string | null; results: Result[]; running: boolean; writes: number } | null;
  staging_url: string | null; writes: boolean };

/** Runs the app on staging and shows what really happens per request, next to what the code map inferred. */
export function Observed({ pid, lead }: { pid: string; lead: boolean }) {
  const { data, reload } = usePoll<X>(`/projects/${pid}/explorer`, 3000);
  const [err, setErr] = useState<string | null>(null);
  if (!data) return null;
  const r = data.run;
  const start = async () => {
    setErr(null);
    try { await post(`/projects/${pid}/explorer`); reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const called = (r?.results ?? []).filter((x) => x.ms != null);
  const failed = called.filter((x) => typeof x.status === "number" && x.status >= 500);
  const skipped = (r?.results ?? []).filter((x) => x.status === "skipped");
  return (
    <section className="flex flex-col gap-4 rounded-2xl border border-line px-5 py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="text-[15px] font-semibold">Observed on staging</h2>
          <p className="max-w-[80ch] text-[13px] text-muted">
            An agent calls each request on staging with real values from the staging database and records which tables it really touched.
            {data.writes ? " Writes are on for this project, on staging only." : " Only GET requests: writes are off."}
          </p>
        </div>
        {lead && data.staging_url && <Button size="sm" disabled={r?.running} onClick={start}><Play size={12} />{r?.running ? "Exploring" : r ? "Run again" : "Explore staging"}</Button>}
      </div>
      {!data.staging_url && <p className="text-[13px] text-muted"><Link href={`/p/${pid}?view=setup`} className="text-text-2 underline-offset-2 hover:underline">Set the staging URL and connect the staging database in Setup</Link> first.</p>}
      {err && <p className="text-xs text-bad">{err}</p>}
      {r && (
        <>
          <p className="text-sm">{r.running ? `Calling ${r.results.length} so far…` : r.summary}</p>
          {failed.length > 0 && (
            <div className="rounded-xl border border-[#4a3a1e] bg-[#1a1712] px-4 py-3 text-[13px]">
              <span className="font-medium text-accent">{failed.length} request{failed.length > 1 ? "s" : ""} failed on staging: </span>
              {failed.map((f) => f.label).join(", ")}. A real record from staging makes {failed.length > 1 ? "them" : "it"} crash, so production will too.
            </div>
          )}
          <table className="w-full table-fixed text-left text-xs">
            <thead><tr className="text-muted">
              <th className="w-[34%] pb-1.5 font-normal">Request</th><th className="w-[9%] pb-1.5 font-normal">Result</th><th className="w-[9%] pb-1.5 text-right font-normal">Time</th>
              <th className="pb-1.5 pl-4 font-normal">Really touched</th>
            </tr></thead>
            <tbody>
              {called.map((x) => {
                const bad = typeof x.status === "number" && x.status >= 400;
                return (
                  <tr key={x.label} className="border-t border-line align-top">
                    <td className="truncate py-1.5 pr-2 font-mono text-[11px]">{x.label}</td>
                    <td className={`py-1.5 font-mono ${bad ? "text-bad" : "text-ok"}`}>{x.status}</td>
                    <td className={`py-1.5 text-right font-mono ${(x.ms ?? 0) > 200 ? "text-text" : "text-muted"}`}>{x.ms} ms</td>
                    <td className="py-1.5 pl-4 text-text-2">
                      {[...(x.reads ?? []).map((t) => t), ...(x.writes ?? []).map((t) => `${t} (writes)`)].join(", ") || <span className="text-muted">no queries</span>}
                      {(x.only_inferred?.length ?? 0) > 0 && <span className="block text-[11px] text-muted">Code map also expected {x.only_inferred!.join(", ")}, not touched here</span>}
                      {(x.only_observed?.length ?? 0) > 0 && <span className="block text-[11px] text-muted">Not in the code map: {x.only_observed!.join(", ")}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {skipped.length > 0 && <p className="text-xs text-muted">Skipped {skipped.length}: {skipped.slice(0, 3).map((s) => `${s.label} (${s.note})`).join("; ")}{skipped.length > 3 ? "…" : ""}</p>}
        </>
      )}
    </section>
  );
}
