"use client";

import * as React from "react";
import { Download, Paperclip, Send } from "lucide-react";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { StatusPill, DetailList, fmtDate, fmtBytes } from "@/components/resource/helpers";
import { useApi } from "@/hooks/use-api";
import { patch, post, ApiError } from "@/lib/http";
import { PriorityBadge, SlaBadge, downloadUrl, uploadAttachment, type Meta, type Sla } from "./common";

interface Att { id: string; name: string; size: number; internal?: boolean; message_id: string | null }
interface Msg { id: string; author_type: string; author_name: string; body: string; created_at: string; internal?: boolean; attachments: Att[] }
interface Detail {
  id: string; number: number; subject: string; status: string; priority: string; category: string; department: string | null;
  department_id?: string | null; assignee_id?: string | null; assignee_name?: string | null; sla?: Sla; created_at: string; created_by_name?: string | null;
  messages: Msg[]; attachments: Att[]; history: { id: string; kind: string; created_at: string; detail: Record<string, string>; actor_name?: string | null; internal?: boolean }[];
}

/** Ticket drawer. `staff` unlocks internal notes, assignment, priority/department and SLA (the API enforces all of it). */
export function TicketDetail({ id, onClose, meta, agents, onChanged }: { id: string | null; onClose: () => void; meta?: Meta; agents?: { id: string; name: string }[]; onChanged: () => void }) {
  const { data, error, loading, reload } = useApi<Detail>(id ? `/support/tickets/${id}` : null);
  const toast = useToast();
  const staff = !!meta?.is_staff;
  const [body, setBody] = React.useState("");
  const [internal, setInternal] = React.useState(false);
  const [file, setFile] = React.useState<File | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    try {
      await fn();
      toast({ title: ok, tone: "success" });
      reload();
      onChanged();
    } catch (e) {
      toast({ title: e instanceof ApiError || e instanceof Error ? e.message : "Request failed", tone: "danger" });
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    if (!id || !body.trim()) return;
    await run(async () => {
      await post(`/support/tickets/${id}/messages`, { body, internal });
      if (file) await uploadAttachment(id, file, internal);
      setBody("");
      setFile(null);
      setInternal(false);
    }, internal ? "Internal note added" : "Reply sent");
  }

  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent title={data ? `#${data.number} ${data.subject}` : "Ticket"} description="Ticket conversation and details" className="w-[min(100vw,40rem)]">
        {loading && <p className="p-5 text-sm text-muted">Loading…</p>}
        {error && <ErrorState onRetry={reload} />}
        {data && (
          <div className="space-y-5 p-5">
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill status={data.status} />
              <PriorityBadge p={data.priority} />
              {staff && <SlaBadge sla={data.sla} status={data.status} />}
              <Badge>{data.category}</Badge>
            </div>

            {staff && meta && (
              <div className="grid gap-2 sm:grid-cols-2">
                <label className="text-xs text-muted">Status
                  <Select aria-label="Status" value={data.status} disabled={busy} onChange={(e) => run(() => patch(`/support/tickets/${data.id}`, { status: e.target.value }), "Status updated")}>
                    {meta.statuses.map((s) => <option key={s}>{s}</option>)}
                  </Select>
                </label>
                <label className="text-xs text-muted">Priority
                  <Select aria-label="Priority" value={data.priority} disabled={busy} onChange={(e) => run(() => patch(`/support/tickets/${data.id}`, { priority: e.target.value }), "Priority updated")}>
                    {meta.priorities.map((s) => <option key={s}>{s}</option>)}
                  </Select>
                </label>
                <label className="text-xs text-muted">Assignee
                  <Select aria-label="Assignee" value={data.assignee_id ?? ""} disabled={busy} onChange={(e) => run(() => post(`/support/tickets/${data.id}/assign`, { assignee_id: e.target.value || null }), "Assignment updated")}>
                    <option value="">Unassigned</option>
                    {agents?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </Select>
                </label>
                <label className="text-xs text-muted">Department
                  <Select aria-label="Department" value={data.department_id ?? ""} disabled={busy} onChange={(e) => run(() => patch(`/support/tickets/${data.id}`, { department_id: e.target.value || null }), "Department updated")}>
                    <option value="">None</option>
                    {meta.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </Select>
                </label>
              </div>
            )}

            {!staff && (
              <div className="flex gap-2">
                {data.status !== "closed" ? (
                  <Button size="sm" variant="outline" loading={busy} onClick={() => run(() => patch(`/support/tickets/${data.id}`, { status: "closed" }), "Ticket closed")}>Close ticket</Button>
                ) : (
                  <Button size="sm" variant="outline" loading={busy} onClick={() => run(() => patch(`/support/tickets/${data.id}`, { status: "open" }), "Ticket reopened")}>Reopen</Button>
                )}
              </div>
            )}

            <ol className="space-y-3" aria-label="Conversation">
              {data.messages.map((m) => (
                <li key={m.id} className={`rounded-lg border p-3 text-sm ${m.internal ? "border-warning/50 bg-warning/10" : m.author_type === "staff" ? "border-primary/30 bg-subtle" : "border-border bg-surface"}`}>
                  <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
                    <span className="font-medium text-text">{m.author_name}</span>
                    <span>{m.internal && <Badge tone="warning">Internal note — not visible to customer</Badge>} {fmtDate(m.created_at)}</span>
                  </div>
                  <p className="break-words whitespace-pre-wrap text-text">{m.body}</p>
                  {m.attachments.map((a) => (
                    <a key={a.id} href={downloadUrl(a.id)} className="mt-2 mr-2 inline-flex items-center gap-1 text-xs text-primary underline" download>
                      <Download className="h-3 w-3" /> {a.name} ({fmtBytes(a.size)}){a.internal ? " · internal" : ""}
                    </a>
                  ))}
                </li>
              ))}
            </ol>

            {data.attachments.filter((a) => !a.message_id).length > 0 && (
              <div className="text-sm">
                <h3 className="mb-1 font-medium text-text">Attachments</h3>
                {data.attachments.filter((a) => !a.message_id).map((a) => (
                  <a key={a.id} href={downloadUrl(a.id)} download className="mr-3 inline-flex items-center gap-1 text-xs text-primary underline">
                    <Paperclip className="h-3 w-3" /> {a.name} ({fmtBytes(a.size)})
                  </a>
                ))}
              </div>
            )}

            {data.status !== "closed" && (
              <div className="space-y-2">
                <label htmlFor="reply-body" className="text-sm font-medium text-text">{internal ? "Internal note" : "Reply"}</label>
                <textarea id="reply-body" rows={4} value={body} onChange={(e) => setBody(e.target.value)} maxLength={10000}
                  className={`w-full rounded-md border bg-surface px-3 py-2 text-base text-text ${internal ? "border-warning" : "border-border-strong"}`} />
                <div className="flex flex-wrap items-center gap-3">
                  {staff && (
                    <label className="flex items-center gap-2 text-sm text-text">
                      <input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" checked={internal} onChange={(e) => setInternal(e.target.checked)} />
                      Internal note (hidden from customer)
                    </label>
                  )}
                  <label className="inline-flex min-h-11 cursor-pointer items-center gap-1 text-sm text-muted">
                    <Paperclip className="h-4 w-4" />
                    <input type="file" className="sr-only" accept={meta?.allowed_extensions.map((e) => `.${e}`).join(",")} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
                    {file ? file.name : `Attach file (max ${fmtBytes(meta?.max_attachment_bytes)})`}
                  </label>
                  <Button className="ml-auto" size="sm" loading={busy} disabled={!body.trim()} onClick={send}><Send className="h-4 w-4" /> {internal ? "Add note" : "Send reply"}</Button>
                </div>
              </div>
            )}

            <section>
              <h3 className="mb-1 text-sm font-medium text-text">History</h3>
              <DetailList items={data.history.map((h) => [`${fmtDate(h.created_at)}${h.internal ? " (internal)" : ""}`, `${h.kind.replace(/_/g, " ")}${h.detail?.to ? ` → ${h.detail.to}` : ""}${h.actor_name ? ` · ${h.actor_name}` : ""}`])} />
            </section>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
