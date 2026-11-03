"use client";

import * as React from "react";
import { Check, Megaphone } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge, type Tone } from "@/components/ui/badge";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { ConfirmDialog } from "@/components/ui/dialog";
import { FormDialog } from "@/components/resource/form-dialog";
import { LiveBadge, StatusPill, fmtDate } from "@/components/resource/helpers";
import { useAuth } from "@/components/providers/auth-provider";
import { useApi } from "@/hooks/use-api";
import { del, post, put } from "@/lib/http";

interface Item { id: string; title: string; body: string; severity: string; read: boolean; acknowledged: boolean; require_ack: boolean; publish_at: string | null; created_at: string }
interface Feed { unread: number; pending_ack: number; items: Item[] }
interface Admin extends Item { status: string; state: string; audience: { roles?: string[]; tenant_ids?: string[] }; expires_at: string | null }
const SEV: Record<string, Tone> = { info: "info", warning: "warning", critical: "danger" };

export function AnnouncementsPanel() {
  const { can } = useAuth();
  const manager = can("announcements:write");
  const feed = useApi<Feed>("/announcements/feed");
  const admin = useApi<Admin[]>(manager ? "/announcements" : null);
  const [editing, setEditing] = React.useState<Admin | "new" | null>(null);
  const [removing, setRemoving] = React.useState<Admin | null>(null);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Announcements" description="Platform notices addressed to you." actions={<><LiveBadge />{manager && <Button size="sm" onClick={() => setEditing("new")}>New announcement</Button>}</>} />
        <CardBody className="space-y-3">
          {feed.loading && <p className="text-sm text-muted">Loading…</p>}
          {feed.error && <ErrorState onRetry={feed.reload} />}
          {feed.data?.items.length === 0 && <EmptyState title="No announcements" description="Platform notices will appear here." />}
          {feed.data?.items.map((a) => (
            <article key={a.id} className="rounded-lg border border-border p-3">
              <div className="flex flex-wrap items-center gap-2"><Megaphone className="h-4 w-4 text-muted" /><h3 className="font-medium text-text">{a.title}</h3><Badge tone={SEV[a.severity]}>{a.severity}</Badge>{!a.read && <Badge tone="primary">new</Badge>}</div>
              <p className="mt-1 text-sm break-words whitespace-pre-wrap text-text">{a.body}</p>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
                <span>{fmtDate(a.publish_at ?? a.created_at)}</span>
                {!a.read && <Button size="sm" variant="ghost" onClick={async () => { await post(`/announcements/${a.id}/read`); feed.reload(); }}>Mark read</Button>}
                {a.require_ack && (a.acknowledged ? <Badge tone="success"><Check className="h-3 w-3" /> Acknowledged</Badge> : <Button size="sm" onClick={async () => { await post(`/announcements/${a.id}/ack`); feed.reload(); }}>Acknowledge</Button>)}
              </div>
            </article>
          ))}
        </CardBody>
      </Card>

      {manager && (
        <Card>
          <CardHeader title="Manage announcements" description="Drafts, scheduled and published notices. Leave audience empty to reach everyone." />
          <CardBody className="space-y-2">
            {admin.data?.map((a) => (
              <div key={a.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm">
                <div className="min-w-0"><span className="font-medium text-text">{a.title}</span> <StatusPill status={a.state} /> <span className="text-xs text-muted">{(a.audience.roles ?? []).join(", ") || "all roles"}{a.audience.tenant_ids?.length ? ` · ${a.audience.tenant_ids.length} tenant(s)` : ""}</span></div>
                <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => setEditing(a)}>Edit</Button><Button size="sm" variant="outline" onClick={() => setRemoving(a)}>Delete</Button></div>
              </div>
            ))}
            {admin.data?.length === 0 && <p className="text-sm text-muted">No announcements yet.</p>}
          </CardBody>
        </Card>
      )}

      <FormDialog
        open={editing !== null}
        onOpenChange={(o) => !o && setEditing(null)}
        title={editing === "new" ? "New announcement" : "Edit announcement"}
        fields={[
          { name: "title", label: "Title", required: true, defaultValue: editing && editing !== "new" ? editing.title : "" },
          { name: "body", label: "Message", type: "textarea", required: true, defaultValue: editing && editing !== "new" ? editing.body : "" },
          { name: "severity", label: "Severity", type: "select", required: true, defaultValue: editing && editing !== "new" ? editing.severity : "info", options: ["info", "warning", "critical"].map((v) => ({ value: v, label: v })) },
          { name: "status", label: "Status", type: "select", required: true, defaultValue: editing && editing !== "new" ? editing.status : "draft", options: ["draft", "published", "archived"].map((v) => ({ value: v, label: v })) },
          { name: "publish_at", label: "Publish at (ISO, optional — schedules a published notice)", placeholder: "2026-12-01T09:00:00Z" },
          { name: "roles", label: "Audience roles (comma separated, optional)", placeholder: "customer,reseller", defaultValue: editing && editing !== "new" ? (editing.audience.roles ?? []).join(",") : "" },
          { name: "tenant_ids", label: "Audience tenant ids (comma separated, optional)", defaultValue: editing && editing !== "new" ? (editing.audience.tenant_ids ?? []).join(",") : "" },
          { name: "require_ack", label: "Require acknowledgement", type: "checkbox", defaultValue: editing && editing !== "new" ? editing.require_ack : false },
        ]}
        onSubmit={async (v) => {
          const split = (s: unknown) => String(s ?? "").split(",").map((x) => x.trim()).filter(Boolean);
          const body = { ...v, roles: split(v.roles), tenant_ids: split(v.tenant_ids), publish_at: v.publish_at || null };
          if (editing === "new") await post("/announcements", body);
          else if (editing) await put(`/announcements/${editing.id}`, body);
          admin.reload();
          feed.reload();
        }}
      />
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title="Delete announcement?" description="Readers will no longer see it. This cannot be undone." confirmLabel="Delete" destructive
        onConfirm={async () => { if (removing) await del(`/announcements/${removing.id}`); admin.reload(); feed.reload(); }} />
    </div>
  );
}
