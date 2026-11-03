import { cn } from "@/lib/cn";

/** KVL mark: a rounded tile with a stylised K built from a stem and two arms. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("h-8 w-8 shrink-0", className)} aria-hidden>
      <rect width="32" height="32" rx="8" fill="var(--primary)" />
      <path d="M11 8v16" stroke="var(--primary-fg)" strokeWidth="3" strokeLinecap="round" />
      <path d="M20.5 8.5 12.5 16l8 7.5" stroke="var(--primary-fg)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </svg>
  );
}

export function Brand({ collapsed }: { collapsed?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <BrandMark />
      {!collapsed && (
        <div className="leading-tight">
          <p className="text-lg font-semibold tracking-tight text-text">KVL</p>
          <p className="text-[11px] font-medium tracking-wide text-muted uppercase">Hosting Panel</p>
        </div>
      )}
    </div>
  );
}
