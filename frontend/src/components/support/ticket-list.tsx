"use client";

import * as React from "react";
import { Download, Plus, RefreshCw } from "lucide-react";
import { Card, CardBody } from "@/components/ui/card";
import { Button, IconButton } from "@/components/ui/button";
import { Select, SearchInput } from "@/components/ui/input";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { FormDialog } from "@/components/resource/form-dialog";
import { LiveBadge, StatusPill, fmtDate } from "@/components/resource/helpers";
import { useApi } from "@/hooks/use-api";
import { post } from "@/lib/http";
import { PriorityBadge, SlaBadge, uploadAttachment, type Meta, type Paged, type TicketRow } from "./common";
import { TicketDetail } from "./ticket-detail";

/** Agent inbox (staff) or "my tickets" (customer) with server-side filters, search and pagination. */
export function TicketList({ tenantFilter = false }: { tenantFilter?: boolean }) {
  const meta = useApi<Meta>("/support/meta");
  const staff = !!meta.data?.is_staff;
  const agents = useApi<{ id: string; name: string }[]>(staff ? "/support/agents" : null);
  const [q, setQ] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [priority, setPriority] = React.useState("");
  const [assignee, setAssignee] = React.useState("");
  const [sla, setSla] = React.useState("");
  const [sort, setSort] = React.useState("activity");
  const [page, setPage] = React.useState(1);
  const [size, setSize] = React.useState(10);
  const [open, setOpen] = React.useState<string | null>(null);
  const [creating, setCreating] = React.useState(false);
  const [file, setFile] = React.useState<File | null>(null);

  const list = useApi<Paged<TicketRow>>(
    "/support/tickets",
    { q, status, priority, assignee: staff ? assignee : "", sla: staff ? sla : "", sort, limit: size, offset: (page - 1) * size },
    { refreshMs: 60000 },
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex flex-wrap items-end gap-2 pt-4">
          <SearchInput value={q} onChange={(v) => { setQ(v); setPage(1); }} placeholder="Search subject, message, #number…" className="min-w-48 flex-1" />
          <Select aria-label="Status filter" className="w-36" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="">All statuses</option>
            {meta.data?.statuses.map((s) => <option key={s}>{s}</option>)}
          </Select>
          <Select aria-label="Priority filter" className="w-36" value={priority} onChange={(e) => { setPriority(e.target.value); setPage(1); }}>
            <option value="">All priorities</option>
            {meta.data?.priorities.map((s) => <option key={s}>{s}</option>)}
          </Select>
          {staff && (
            <>
              <Select aria-label="Assignee filter" className="w-40" value={assignee} onChange={(e) => { setAssignee(e.target.value); setPage(1); }}>
                <option value="">Any assignee</option>
                <option value="me">Assigned to me</option>
                <option value="unassigned">Unassigned</option>
                {agents.data?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
              <Select aria-label="SLA filter" className="w-36" value={sla} onChange={(e) => { setSla(e.target.value); setPage(1); }}>
                <option value="">Any SLA</option>
                <option value="breached">Breached</option>
              </Select>
              <Select aria-label="Sort" className="w-36" value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }}>
                <option value="activity">Latest activity</option>
                <option value="priority">Priority</option>
                <option value="created">Newest</option>
              </Select>
            </>
          )}
          <LiveBadge />
          <IconButton label="Refresh" variant="outline" onClick={list.reload}><RefreshCw className="h-4 w-4" /></IconButton>
          <Button asChild variant="outline" size="sm"><a href={`/api/v1/support/tickets/export.csv?${new URLSearchParams({ status, priority, q }).toString()}`} download><Download className="h-4 w-4" /> CSV</a></Button>
          <Button size="sm" onClick={() => setCreating(true)}><Plus className="h-4 w-4" /> New ticket</Button>
        </CardBody>
      </Card>

      <Card className="overflow-hidden">
        {list.loading && <p className="p-5 text-sm text-muted">Loading tickets…</p>}
        {list.error && <ErrorState onRetry={list.reload} />}
        {list.data && list.data.items.length === 0 && <EmptyState title="No tickets match" description="Adjust the filters or open a new ticket." />}
        {list.data && list.data.items.length > 0 && (
          <ul className="divide-y divide-border">
            {list.data.items.map((t) => (
              <li key={t.id}>
                <button type="button" onClick={() => setOpen(t.id)} className="flex min-h-11 w-full flex-col gap-1.5 px-4 py-3 text-left hover:bg-subtle focus-visible:bg-subtle">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="tnum text-xs text-muted">#{t.number}</span>
                    <span className="font-medium text-text">{t.subject}</span>
                  </span>
                  <span className="flex flex-wrap items-center gap-2 text-xs text-muted">
                    <StatusPill status={t.status} />
                    <PriorityBadge p={t.priority} />
                    {staff && <SlaBadge sla={t.sla} status={t.status} />}
                    <span>{t.category}</span>
                    {t.department && <span>· {t.department}</span>}
                    {staff && <span>· {t.assignee_name ?? "Unassigned"}</span>}
                    <span>· {fmtDate(t.last_activity_at)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {list.data && <Pagination page={page} pageSize={size} total={list.data.total} onPage={setPage} onPageSize={(s) => { setSize(s); setPage(1); }} />}
      </Card>

      <TicketDetail id={open} onClose={() => setOpen(null)} meta={meta.data} agents={agents.data} onChanged={list.reload} />

      <FormDialog
        open={creating}
        onOpenChange={setCreating}
        title="New support ticket"
        description="Describe the problem. You can attach a screenshot or log (png, jpg, gif, webp, pdf, txt, log, csv)."
        submitLabel="Submit ticket"
        successMessage="Ticket created"
        fields={[
          ...(tenantFilter ? [{ name: "tenant_id", label: "Customer (tenant id)", required: true, hint: "Platform staff open tickets on behalf of a tenant." }] : []),
          { name: "subject", label: "Subject", required: true },
          { name: "category", label: "Category", type: "select" as const, required: true, defaultValue: "general", options: (meta.data?.categories ?? ["general"]).map((c) => ({ value: c, label: c })) },
          { name: "priority", label: "Priority", type: "select" as const, required: true, defaultValue: "normal", options: (staff ? (meta.data?.priorities ?? []) : ["low", "normal", "high"]).map((c) => ({ value: c, label: c })) },
          { name: "body", label: "Details", type: "textarea" as const, required: true },
        ]}
        onSubmit={async (v) => {
          const created = await post<{ id: string }>("/support/tickets", { ...v, tenant_id: tenantFilter ? v.tenant_id : undefined });
          if (file) await uploadAttachment(created.id, file);
          setFile(null);
          list.reload();
        }}
      />
      {creating && (
        <div className="fixed bottom-20 left-1/2 z-[80] -translate-x-1/2 rounded-md border border-border bg-elevated px-3 py-2 text-sm shadow-lg md:bottom-6">
          <label className="inline-flex min-h-11 cursor-pointer items-center gap-2 text-muted">
            Attachment:
            <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} accept={meta.data?.allowed_extensions.map((e) => `.${e}`).join(",")} />
          </label>
        </div>
      )}
    </div>
  );
}
