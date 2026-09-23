"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type ClusterRow = {
  id: string;
  title: string;
  count: number;
  status: string;
  owner: string | null;
  agent: string | null;
  client_affected: boolean;
  step: string;
  first_seen: string;
  top_theory: string | null;
  confidence: number | null;
};

export type Theory = {
  id: string;
  text: string;
  confidence: number;
  cites: string[];
  check_sql: string | null;
  status: "open" | "confirmed" | "ruled_out" | "refuted";
  ruled_out_by: string | null;
  reason: string | null;
  check_result: string | null;
};

export type EvidenceStep = { name: string; source: string; summary: string; cites: string[] };

export type ClusterDetail = ClusterRow & {
  service: string;
  fields: string[];
  clients: string[];
  theories: Theory[];
  evidence: EvidenceStep[];
  cards: Record<string, string>;
  evidence_ms: number;
  assignment: { person: string; handover: string | null } | null;
  handover: string | null;
  sample_tickets: string[];
};

export type Approval = {
  id: string;
  kind: "run_check" | "assign" | "client_update" | "decision" | "help" | "rollback";
  agent: string;
  cluster: string | null;
  title: string;
  detail: string;
  payload: Record<string, unknown>;
  needs: string;
};

export type Person = {
  id: string; name: string; role: string; level: string; shift: string; hours: number;
  over_cap: boolean; client_facing: boolean; doing: string | null;
};

export type Agent = { id: string; name: string; short: string; trust: string; activity: string };

export type Line = { id: string; speaker: string; text: string; time: string; kind: string };

export type State = {
  project: string;
  company: string;
  clock: string;
  memory: { backend: "moss" | "local"; p50_ms: number; searches: number; per_min: number; indexes: number; cards: number };
  model: { provider: string; online: boolean; used: number; budget: number; calls: number; cached: number };
  sim: { running: boolean; done: number; total: number };
  tickets: number;
  clusters: ClusterRow[];
  confirmed: number;
  people: Person[];
  agents: Agent[];
  approvals: Approval[];
  transcript: Line[];
  decisions: { id: string; text: string; by: string }[];
};

export function currentUser(): string | null {
  try { return localStorage.getItem("wr_user"); } catch { return null; }
}

export function setCurrentUser(id: string | null) {
  try { if (id) localStorage.setItem("wr_user", id); else localStorage.removeItem("wr_user"); } catch { /* private mode */ }
}

/** Ends the session on the server (the cookie is HttpOnly, so only the server can clear it). */
export async function signOut() {
  setCurrentUser(null);
  try { await fetch("/api/auth/logout", { method: "POST" }); } catch { /* signed out locally either way */ }
  location.href = "/login";
}

function headers(json = false): Record<string, string> {
  const h: Record<string, string> = {};
  const u = currentUser();
  if (u) h["x-user"] = u;
  if (json) h["content-type"] = "application/json";
  return h;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function send(method: string, path: string, body?: unknown) {
  const r = await fetch(`/api${path}`, { method, headers: headers(body !== undefined), body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : msg; } catch { /* not json */ }
    throw new ApiError(r.status, msg);
  }
  return r.json();
}

export const post = (path: string, body: unknown = {}) => send("POST", path, body);
export const patch = (path: string, body: unknown = {}) => send("PATCH", path, body);
export const get = (path: string) => send("GET", path);
export const del = (path: string) => send("DELETE", path);

/** Poll a JSON endpoint. The board changes every second during a war room. */
export function usePoll<T>(path: string | null, ms = 1000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const r = await fetch(`/api${path}`, { cache: "no-store", headers: headers() });
      if (r.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
        location.href = "/login";
        return;
      }
      if (!r.ok) {
        let msg = String(r.status);
        try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : msg; } catch { /* not json */ }
        throw new Error(msg);
      }
      const json = (await r.json()) as T;
      if (alive.current) {
        setData(json);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : "offline");
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    const first = setTimeout(load, 0);
    const t = setInterval(load, ms);
    return () => {
      alive.current = false;
      clearTimeout(first);
      clearInterval(t);
    };
  }, [load, ms]);

  return { data, error, reload: load };
}

export const STATUS: Record<string, { label: string; tone: "accent" | "ok" | "muted" | "bad" }> = {
  new: { label: "New", tone: "muted" },
  investigating: { label: "Investigating", tone: "accent" },
  needs_check: { label: "Needs check", tone: "accent" },
  confirmed: { label: "Confirmed", tone: "ok" },
  fix_in_review: { label: "Fix in review", tone: "muted" },
  resolved: { label: "Resolved", tone: "ok" },
};
