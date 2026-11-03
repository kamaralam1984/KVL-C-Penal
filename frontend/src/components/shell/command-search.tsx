"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Dialog as D } from "radix-ui";
import { CornerDownLeft, Search } from "lucide-react";
import { ROLES, flatItems, type Role } from "@/config/nav";
import { cn } from "@/lib/cn";

/** Global search entry: Ctrl/⌘+K. Phase 1 searches navigation only; entity search arrives with the APIs. */
export function CommandSearch({ role, open, onOpenChange }: { role: Role; open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const [q, setQ] = React.useState("");
  const [cursor, setCursor] = React.useState(0);
  const base = ROLES[role].base;

  const results = React.useMemo(() => {
    const n = q.trim().toLowerCase();
    return flatItems(role).filter((i) => !n || `${i.label} ${i.description}`.toLowerCase().includes(n));
  }, [q, role]);

  const go = (slug: string) => {
    onOpenChange(false);
    router.push(`${base}/${slug}`);
  };

  return (
    <D.Root
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) {
          setQ("");
          setCursor(0);
        }
      }}
    >
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-[60] bg-black/45 data-[state=open]:animate-[fade-in_var(--dur-base)_var(--ease)]" />
        <D.Content
          className="fixed top-[12vh] left-1/2 z-[61] w-[min(92vw,34rem)] -translate-x-1/2 overflow-hidden rounded-xl border border-border bg-elevated shadow-lg data-[state=open]:animate-[pop-in_var(--dur-fast)_var(--ease)]"
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setCursor((c) => Math.min(c + 1, results.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setCursor((c) => Math.max(c - 1, 0));
            } else if (e.key === "Enter" && results[cursor]) {
              go(results[cursor].slug);
            }
          }}
        >
          <D.Title className="sr-only">Search</D.Title>
          <D.Description className="sr-only">Search pages. Use arrow keys to move and Enter to open.</D.Description>
          <div className="flex items-center gap-2 border-b border-border px-4">
            <Search className="h-4 w-4 text-faint" aria-hidden />
            <input
              autoFocus
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setCursor(0);
              }}
              placeholder="Search pages…"
              aria-label="Search pages"
              role="combobox"
              aria-expanded
              aria-controls="cmd-results"
              className="h-12 flex-1 bg-transparent text-lg text-text outline-none placeholder:text-faint"
            />
            <kbd className="rounded border border-border px-1.5 py-0.5 text-[11px] text-faint">Esc</kbd>
          </div>
          <ul id="cmd-results" role="listbox" aria-label="Pages" className="max-h-80 overflow-y-auto p-1.5">
            {results.length === 0 && <li className="px-3 py-8 text-center text-sm text-muted">No pages match “{q}”.</li>}
            {results.map((r, i) => (
              <li key={r.slug} role="option" aria-selected={i === cursor}>
                <button
                  type="button"
                  onMouseEnter={() => setCursor(i)}
                  onClick={() => go(r.slug)}
                  className={cn("flex w-full items-center gap-3 rounded-md px-3 py-2 text-left", i === cursor && "bg-subtle")}
                >
                  <r.icon className="h-4 w-4 shrink-0 text-muted" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-base font-medium text-text">{r.label}</span>
                    <span className="block truncate text-xs text-muted">{r.description}</span>
                  </span>
                  {i === cursor && <CornerDownLeft className="h-3.5 w-3.5 text-faint" aria-hidden />}
                </button>
              </li>
            ))}
          </ul>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
