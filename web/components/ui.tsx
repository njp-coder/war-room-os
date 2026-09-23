"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

type Tone = "accent" | "ok" | "muted" | "bad" | "agent" | "info";

const toneClass: Record<Tone, string> = {
  accent: "bg-accent-soft text-accent",
  ok: "bg-ok-soft text-ok",
  bad: "bg-bad-soft text-bad",
  agent: "bg-agent-soft text-agent",
  info: "bg-info-soft text-info",
  muted: "bg-raised text-text-2",
};

export function Badge({ tone = "muted", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`inline-flex h-6 items-center rounded-md px-2 text-xs font-medium ${toneClass[tone]}`}>{children}</span>;
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "quiet"; size?: "sm" | "md" };

export function Button({ variant = "ghost", size = "md", className = "", ...rest }: BtnProps) {
  const base = "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium transition-colors disabled:opacity-50";
  const sizes = { sm: "h-8 px-3 text-xs", md: "h-10 px-4 text-sm" };
  const variants = {
    primary: "bg-accent text-accent-ink hover:brightness-110",
    ghost: "border border-line-strong text-text hover:bg-raised",
    quiet: "text-muted hover:text-text",
  };
  return <button type="button" className={`${base} ${sizes[size]} ${variants[variant]} ${className}`} {...rest} />;
}

// A person keeps the same colour everywhere, so a face is recognisable in a row of them. Hue from the name, fixed
// saturation and lightness so every one of them is legible and none of them shouts louder than the accent.
const HUES = [8, 32, 96, 150, 176, 200, 224, 258, 288, 322];

function hue(name: string): number {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return HUES[h % HUES.length];
}

export function Initials({ name, dim = false, size = 28 }: { name: string; dim?: boolean; size?: number }) {
  const text = name === "You" ? "You" : name.split(" ").map((p) => p[0]).slice(0, 2).join("");
  const h = hue(name);
  return (
    <span
      style={{ width: size, height: size, ...(dim ? {} : { background: `hsl(${h} 30% 26%)`, color: `hsl(${h} 55% 86%)` }) }}
      className={`inline-flex shrink-0 items-center justify-center rounded-full border-2 border-bg text-[10px] font-semibold ${dim ? "bg-panel text-muted" : ""}`}
    >
      {text}
    </span>
  );
}

export function AgentMark({ short, active = false }: { short: string; active?: boolean }) {
  return (
    <span
      className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md border font-mono text-[10px] transition-colors ${active ? "border-agent bg-agent-soft text-agent" : "border-line-strong text-muted"}`}
    >
      {short}
    </span>
  );
}

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true" aria-label={title}>
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="enter relative flex h-full w-full max-w-[420px] flex-col border-l border-line bg-panel">
        <div className="flex h-14 items-center justify-between border-b border-line px-5">
          <h2 className="text-sm font-semibold">{title}</h2>
          <Button variant="quiet" size="sm" onClick={onClose}>Close</Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}
