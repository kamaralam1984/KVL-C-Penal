"use client";

import * as React from "react";
import { Badge, type Tone } from "@/components/ui/badge";

export interface Sla {
  first_response: "met" | "breached" | "pending";
  resolution: "met" | "breached" | "pending";
  first_response_due_at: string | null;
  resolution_due_at: string | null;
  escalation_level: number;
}
export interface TicketRow {
  id: string;
  number: number;
  subject: string;
  category: string;
  department: string | null;
  priority: string;
  status: string;
  created_at: string;
  last_activity_at: string;
  tenant_id: string;
  created_by_name?: string | null;
  assignee_id?: string | null;
  assignee_name?: string | null;
  sla?: Sla;
}
export interface Meta {
  categories: string[];
  priorities: string[];
  statuses: string[];
  departments: { id: string; name: string }[];
  is_staff: boolean;
  max_attachment_bytes: number;
  allowed_extensions: string[];
}
export interface Paged<T> {
  total: number;
  items: T[];
}

const PRIORITY: Record<string, Tone> = { low: "neutral", normal: "info", high: "warning", urgent: "danger" };
export const PriorityBadge = ({ p }: { p: string }) => (
  <Badge tone={PRIORITY[p] ?? "neutral"}>{p}</Badge>
);

/** SLA state for staff only (the API never returns `sla` to customers). */
export function SlaBadge({ sla, status }: { sla?: Sla; status: string }) {
  const [now] = React.useState(() => Date.now());
  if (!sla || status === "closed" || status === "resolved") return null;
  const breached = sla.first_response === "breached" || sla.resolution === "breached";
  const due = sla.first_response === "pending" ? sla.first_response_due_at : sla.resolution_due_at;
  const mins = due ? Math.round((new Date(due).getTime() - now) / 60000) : null;
  if (breached) return <Badge tone="danger" dot>SLA breached{sla.escalation_level ? ` · L${sla.escalation_level}` : ""}</Badge>;
  if (mins !== null && mins < 60) return <Badge tone="warning" dot>Due in {Math.max(mins, 0)}m</Badge>;
  return <Badge tone="success" dot>Within SLA</Badge>;
}

function csrf(): string {
  const m = typeof document === "undefined" ? null : document.cookie.match(/(?:^|; )kvl_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

/** Multipart upload (the shared JSON client cannot send files). Throws Error with the API message. */
export async function uploadAttachment(ticketId: string, file: File, internal = false): Promise<void> {
  const fd = new FormData();
  fd.append("file", file);
  if (internal) fd.append("internal", "true");
  const res = await fetch(`/api/v1/support/tickets/${ticketId}/attachments`, { method: "POST", body: fd, credentials: "include", headers: { "X-CSRF-Token": csrf() } });
  if (!res.ok) {
    const j = await res.json().catch(() => null);
    throw new Error(j?.error?.message ?? `Upload failed (${res.status})`);
  }
}

export const downloadUrl = (id: string) => `/api/v1/support/attachments/${id}/download`;
