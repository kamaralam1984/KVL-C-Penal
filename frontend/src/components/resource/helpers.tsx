"use client";

import * as React from "react";
import { Badge, type Tone } from "@/components/ui/badge";
import { PermissionDenied } from "@/components/ui/states";
import { useAuth } from "@/components/providers/auth-provider";

/** Map a backend status string to a badge tone. Unknown statuses stay neutral. */
const TONES: Record<string, Tone> = {
  active: "success", succeeded: "success", healthy: "success", paid: "success", verified: "success", issued: "success", connected: "success", published: "success", resolved: "success", approved: "success", running: "success", ok: "success", valid: "success", completed: "success",
  deploying: "info", ready: "success", rotating: "info", pending_server: "warning", provisioning_blocked: "warning", live: "success", staged: "info", acknowledged: "info", escalated: "danger", breached: "danger", dead_letter: "danger", delivered: "success", retrying: "warning", reconciled: "success", paid_out: "success",
  trial: "info", pending: "info", queued: "info", open: "info", renewing: "info", draft: "neutral", provisioning: "info", processing: "info", in_progress: "info", requested: "info", registered: "info", created: "info",
  warning: "warning", stale: "warning", past_due: "warning", expiring: "warning", unverified: "warning", grace: "warning", on_hold: "warning", pending_approval: "warning",
  suspended: "neutral", disabled: "neutral", archived: "neutral", stopped: "neutral", cancelled: "neutral", closed: "neutral", unknown: "neutral", not_configured: "neutral", disconnected: "neutral",
  failed: "danger", critical: "danger", error: "danger", expired: "danger", timed_out: "danger", rejected: "danger", refunded: "warning", unpaid: "danger", revoked: "danger",
};

export function StatusPill({ status }: { status: string | null | undefined }) {
  const s = (status ?? "unknown").toLowerCase();
  return (
    <Badge tone={TONES[s] ?? "neutral"} dot>
      {s.replace(/_/g, " ")}
    </Badge>
  );
}

/** Render children only when the signed-in role holds the permission (UX only; the API enforces it). */
export function Can({ perm, children, fallback = null }: { perm: string; children: React.ReactNode; fallback?: React.ReactNode }) {
  const { can } = useAuth();
  return <>{can(perm) ? children : fallback}</>;
}

export function RequirePerm({ perm, children }: { perm: string; children: React.ReactNode }) {
  const { can, loading } = useAuth();
  if (loading) return null;
  return can(perm) ? <>{children}</> : <PermissionDenied />;
}

export function LiveBadge({ label = "Live" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-success/12 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-success uppercase" title="Loaded from the control-plane API">
      <span className="icon-pulse h-1.5 w-1.5 rounded-full bg-success" aria-hidden />
      {label}
    </span>
  );
}

export function DetailList({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="divide-y divide-border text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="flex flex-wrap justify-between gap-x-4 gap-y-1 py-2.5">
          <dt className="text-muted">{k}</dt>
          <dd className="max-w-[65%] text-right font-medium break-words text-text">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export const fmtDate = (v?: string | null) => (v ? new Date(v).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");
export const fmtBytes = (n?: number | null) => {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
};
export const fmtMoney = (paise?: number | null, cur = "INR") => (paise == null ? "—" : new Intl.NumberFormat("en-IN", { style: "currency", currency: cur }).format(paise / 100));
