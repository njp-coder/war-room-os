"use client";

import { useRef, useState } from "react";
import { Paperclip, Record, Robot, Stop } from "@phosphor-icons/react";
import { currentUser } from "@/lib/api";
import { Button, Initials } from "./ui";

export type Attachment = { id: string; kind: string; name: string; text: string | null; by: string; by_kind: string; at: number };

async function upload(ownerType: string, ownerId: string, file: Blob, name: string) {
  const fd = new FormData();
  fd.append("file", file, name);
  const u = currentUser();
  const r = await fetch(`/api/evidence/${ownerType}/${ownerId}`, { method: "POST", body: fd, headers: u ? { "x-user": u } : {} });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `Upload failed (${r.status})`);
}

/** Evidence from agents (request/response, query results) and people (screen recordings, screenshots, logs). */
export function Evidence({ ownerType, ownerId, items, canAdd, onChange }: {
  ownerType: "bug" | "test" | "incident"; ownerId: string; items: Attachment[]; canAdd: boolean; onChange: () => void;
}) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const user = currentUser();

  const startRecording = async () => {
    setErr(null);
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      const chunks: Blob[] = [];
      const mr = new MediaRecorder(stream, { mimeType: MediaRecorder.isTypeSupported("video/webm;codecs=vp9") ? "video/webm;codecs=vp9" : "video/webm" });
      mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        setBusy(true);
        try { await upload(ownerType, ownerId, new Blob(chunks, { type: "video/webm" }), `recording-${new Date().toISOString().slice(0, 19)}.webm`); onChange(); }
        catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
        setBusy(false);
      };
      stream.getVideoTracks()[0].addEventListener("ended", () => mr.state !== "inactive" && mr.stop());
      mr.start(1000);
      rec.current = mr;
      setRecording(true);
    } catch {
      setErr("Screen recording was cancelled or isn't allowed in this browser.");
    }
  };

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true); setErr(null);
    try { await upload(ownerType, ownerId, f, f.name); onChange(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  };

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">Evidence ({items.length})</h2>
        {canAdd && (
          <div className="flex gap-2">
            {recording ? (
              <Button size="sm" variant="primary" onClick={() => rec.current?.stop()}><Stop size={12} /> Stop and attach</Button>
            ) : (
              <Button size="sm" disabled={busy} onClick={startRecording}><Record size={12} className="text-bad" /> Record screen</Button>
            )}
            <Button size="sm" disabled={busy} onClick={() => fileRef.current?.click()}><Paperclip size={12} /> Attach file</Button>
            <input ref={fileRef} type="file" className="hidden" accept="video/*,image/*,.har,.log,.txt,.json" onChange={(e) => onFile(e.target.files?.[0])} />
          </div>
        )}
      </div>
      {recording && <p className="text-xs text-bad">Recording. Do the steps, then press Stop and attach.</p>}
      {busy && <p className="text-xs text-muted">Uploading</p>}
      {err && <p className="text-xs text-bad">{err}</p>}
      {items.length === 0 && <p className="text-sm text-muted">No evidence yet. Recordings and screenshots from testers, and the agent&apos;s requests and query results, show up here.</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        {items.map((a) => (
          <figure key={a.id} className="flex flex-col gap-2 rounded-lg border border-line p-3">
            {a.kind === "video" && <video controls className="w-full rounded bg-black" src={`/api/files/${a.id}?u=${user}`} />}
            {a.kind === "image" && <img alt={a.name} className="w-full rounded" src={`/api/files/${a.id}?u=${user}`} />}
            {a.kind === "text" && <pre className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-text-2">{a.text}</pre>}
            {a.kind === "file" && <a className="text-sm text-accent" href={`/api/files/${a.id}?u=${user}`}>{a.name}</a>}
            <figcaption className="flex items-center gap-2 text-[11px] text-muted">
              {a.by_kind === "agent" ? <Robot size={12} className="text-agent" /> : <Initials name={a.by} size={16} />}
              {a.by}, {new Date(a.at * 1000).toLocaleString()}
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}
