"use client";

import { Robot } from "@phosphor-icons/react";
import { Initials } from "./ui";

export type RelayItem = { step: string; actor: string; kind: string; note: string; at: number };

export const BUG_STAGES = ["reported", "reproduced", "cause_found", "proposed", "fixing", "fixed", "verified"];
export const BUG_STAGE_LABEL: Record<string, string> = {
  reported: "Reported", reproduced: "Reproduced", cause_found: "Cause found", proposed: "Owner proposed",
  fixing: "Fixing", fixed: "Fixed", verified: "Verified",
};
// Who normally does each step: agents do the legwork, people make the calls.
export const STEP_OWNER: Record<string, "agent" | "human" | "either"> = {
  reported: "either", reproduced: "agent", cause_found: "agent", proposed: "agent", fixing: "human", fixed: "human", verified: "human",
};

export function Actor({ item, size = 28 }: { item?: RelayItem; size?: number }) {
  if (!item) return <span style={{ width: size, height: size }} className="inline-block shrink-0 rounded-full border border-dashed border-line-strong" />;
  if (item.kind === "agent") {
    return (
      <span style={{ width: size, height: size }} title={item.actor}
        className="inline-flex shrink-0 items-center justify-center rounded-full bg-agent-soft text-agent">
        <Robot size={Math.round(size * 0.55)} />
      </span>
    );
  }
  return <Initials name={item.actor} size={size} />;
}

/** The full relay: every step, who did it, what they found. */
export function RelayTrack({ relay, stage }: { relay: RelayItem[]; stage: string }) {
  const at = BUG_STAGES.indexOf(stage);
  const byStep = Object.fromEntries(relay.map((r) => [r.step, r]));
  return (
    <ol className="grid grid-cols-2 gap-x-3 gap-y-6 sm:grid-cols-4 lg:grid-cols-7">
      {BUG_STAGES.map((s, i) => {
        const item = byStep[s];
        const done = i <= at && item;
        const current = i === at + 1 || (i === at && s === "verified");
        return (
          <li key={s} className="flex min-w-0 flex-col gap-2">
            <div className="flex items-center gap-2">
              {/* Pulsing ring on the step in progress: amber only when it's a person's turn. */}
              <span className={`relative ${current && !done ? `after:absolute after:-inset-1 after:animate-ping after:rounded-full after:border ${STEP_OWNER[s] === "agent" ? "after:border-agent/70" : "after:border-accent/60"}` : ""}`}>
                <Actor item={done ? item : undefined} />
              </span>
              <span className={`h-px flex-1 ${i < at ? "bg-ok/40" : "bg-line-strong"} ${i === BUG_STAGES.length - 1 ? "invisible" : ""}`} />
            </div>
            <div className="min-w-0">
              <p className={`text-xs font-medium ${done ? "text-text" : "text-muted"}`}>{BUG_STAGE_LABEL[s]}</p>
              <p className="truncate text-[11px] text-muted">
                {done ? item.actor : STEP_OWNER[s] === "agent" ? "An agent" : STEP_OWNER[s] === "human" ? (s === "verified" ? "A tester" : "The owner") : "Anyone"}
              </p>
              {done && item.note && <p className="mt-1 line-clamp-3 text-[11px] leading-snug text-text-2">{item.note}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** Seven dots for a card: violet = an agent did it, green = a person did it. */
export function RelayDots({ relay, stage }: { relay: RelayItem[]; stage: string }) {
  const at = BUG_STAGES.indexOf(stage);
  const byStep = Object.fromEntries(relay.map((r) => [r.step, r]));
  return (
    <span className="flex items-center gap-1" aria-label={`Stage: ${BUG_STAGE_LABEL[stage]}`}>
      {BUG_STAGES.map((s, i) => {
        const item = byStep[s];
        const cls = i <= at && item ? (item.kind === "agent" ? "bg-agent" : "bg-ok") : "bg-line-strong";
        return <span key={s} title={`${BUG_STAGE_LABEL[s]}${item && i <= at ? `: ${item.actor}` : ""}`} className={`h-1.5 w-3 rounded-full ${cls}`} />;
      })}
    </span>
  );
}
