"use client";

import { useState } from "react";
import { Robot } from "@phosphor-icons/react";
import { post } from "@/lib/api";
import { Button, Initials } from "./ui";

export type Note = { id: string; author: string; author_kind: string; text: string; ask: string | null; at: number };

/** Where agents ask for help and people answer. */
export function Thread({ ownerType, ownerId, notes, canWrite, onChange }: {
  ownerType: "bug" | "test" | "incident"; ownerId: string; notes: Note[]; canWrite: boolean; onChange: () => void;
}) {
  const [text, setText] = useState("");
  const send = async () => {
    if (!text.trim()) return;
    await post(`/thread/${ownerType}/${ownerId}`, { text });
    setText("");
    onChange();
  };
  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-sm font-semibold">Thread</h2>
      {notes.length === 0 && <p className="text-sm text-muted">Agents post here when they need a person. Reply to guide them.</p>}
      <ol className="flex flex-col gap-3">
        {notes.map((n) => (
          <li key={n.id} className={`flex gap-3 ${n.author_kind === "agent" && n.ask ? "rounded-lg border border-accent/40 bg-accent-soft/30 p-3" : ""}`}>
            {n.author_kind === "agent"
              ? <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-agent-soft text-agent"><Robot size={15} /></span>
              : <Initials name={n.author} size={28} />}
            <div className="min-w-0">
              <p className="text-[11px] text-muted">{n.author}{n.author_kind === "agent" && n.ask ? ", asking for help" : ""}, {new Date(n.at * 1000).toLocaleTimeString()}</p>
              <p className="text-sm leading-relaxed">{n.text}</p>
            </div>
          </li>
        ))}
      </ol>
      {canWrite && (
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <label htmlFor={`reply-${ownerId}`} className="sr-only">Reply</label>
          <input id={`reply-${ownerId}`} value={text} onChange={(e) => setText(e.target.value)} placeholder="Reply to the agent or the team"
            className="h-10 min-w-0 flex-1 rounded-lg border border-line-strong bg-raised px-3 text-sm text-text placeholder:text-muted focus:border-accent focus:outline-none" />
          <Button type="submit">Send</Button>
        </form>
      )}
    </section>
  );
}
