import { cn } from "@/lib/cn";

export type Tone = "neutral" | "primary" | "success" | "warning" | "danger" | "info";

const tones: Record<Tone, string> = {
  neutral: "bg-subtle text-muted",
  primary: "bg-primary-soft text-primary",
  success: "bg-success/12 text-success",
  warning: "bg-warning/14 text-warning",
  danger: "bg-danger/12 text-danger",
  info: "bg-info/12 text-info",
};

const dots: Record<Tone, string> = {
  neutral: "bg-faint",
  primary: "bg-primary",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  info: "bg-info",
};

export function Badge({
  tone = "neutral",
  dot,
  className,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        tones[tone],
        className,
      )}
    >
      {dot && <span className={cn("h-1.5 w-1.5 rounded-full", dots[tone])} aria-hidden />}
      {children}
    </span>
  );
}

/** Status is always conveyed by text + dot, never colour alone. */
export type Status = "healthy" | "active" | "warning" | "critical" | "pending" | "unknown" | "suspended";

const statusMap: Record<Status, { tone: Tone; label: string }> = {
  healthy: { tone: "success", label: "Healthy" },
  active: { tone: "success", label: "Active" },
  warning: { tone: "warning", label: "Warning" },
  critical: { tone: "danger", label: "Critical" },
  pending: { tone: "info", label: "Pending" },
  suspended: { tone: "neutral", label: "Suspended" },
  unknown: { tone: "neutral", label: "Unknown" },
};

export function StatusBadge({ status, label }: { status: Status; label?: string }) {
  const s = statusMap[status];
  return (
    <Badge tone={s.tone} dot>
      {label ?? s.label}
    </Badge>
  );
}

/** Marks any sample/fixture content so it can never be mistaken for live data. */
export function DemoBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border border-dashed border-warning/60 bg-warning/10 px-1.5 py-0.5",
        "text-[10px] font-semibold tracking-wider text-warning uppercase",
        className,
      )}
      title="Sample data for layout preview only. Not connected to a live source."
    >
      Demo data
    </span>
  );
}
