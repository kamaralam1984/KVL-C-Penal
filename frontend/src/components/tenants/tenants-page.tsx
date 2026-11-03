"use client";

import * as React from "react";
import { Download, Eye, Pause, Play, XCircle } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { ResourceTable } from "@/components/resource/resource-table";
import { FormDialog, type FieldDef } from "@/components/resource/form-dialog";
import { Can, DetailList, RequirePerm, StatusPill, fmtDate } from "@/components/resource/helpers";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Button, IconButton } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Field, Input } from "@/components/ui/input";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { useApi } from "@/hooks/use-api";
import { useToast } from "@/components/ui/toast";
import { get, post, put } from "@/lib/http";
import type { Column, FilterDef } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/states";
import { TableSkeleton } from "@/components/ui/skeleton";

interface Tenant { id: string; name: string; slug: string; status: string; contact_email: string | null; plan_code: string | null; trial_ends_at: string | null; grace_ends_at: string | null; suspended_at: string | null; cancelled_at: string | null; retention_until: string | null; created_at: string }
interface Quota { resource: string; limit: number; used: number }

const columns: Column<Tenant>[] = [
  { id: "name", header: "Organization", sortValue: (r) => r.name, cell: (r) => (<div><p className="font-medium text-text">{r.name}</p><p className="text-xs text-muted">{r.slug}</p></div>) },
  { id: "status", header: "Status", sortValue: (r) => r.status, cell: (r) => <StatusPill status={r.status} /> },
  { id: "plan", header: "Plan", hideOnMobile: true, sortValue: (r) => r.plan_code ?? "", cell: (r) => r.plan_code ?? "—" },
  { id: "contact", header: "Contact", hideOnMobile: true, cell: (r) => r.contact_email ?? "—" },
  { id: "trial", header: "Trial ends", hideOnMobile: true, defaultHidden: true, cell: (r) => fmtDate(r.trial_ends_at) },
  { id: "created", header: "Created", hideOnMobile: true, sortValue: (r) => r.created_at, cell: (r) => fmtDate(r.created_at) },
];
const filters: FilterDef<Tenant>[] = [{ id: "status", label: "Statuses", options: ["pending", "trial", "active", "suspended", "cancelled"], match: (r, v) => r.status === v }];

const createFields: FieldDef[] = [
  { name: "name", label: "Organization name", required: true },
  { name: "contact_email", label: "Contact email", type: "email" },
  { name: "plan_code", label: "Plan code", hint: "Optional. Plans are managed under Plans." },
  { name: "status", label: "Initial status", type: "select", required: true, defaultValue: "trial", options: [{ value: "trial", label: "Trial" }, { value: "active", label: "Active" }, { value: "pending", label: "Pending" }] },
];

function QuotaRow({ q, tenantId, onSaved }: { q: Quota; tenantId: string; onSaved: () => void }) {
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [v, setV] = React.useState(String(q.limit));
  const pct = q.limit ? Math.min(100, (q.used / q.limit) * 100) : 100;
  return (
    <li className="py-3">
      <div className="mb-1.5 flex items-center justify-between text-sm">
        <span className="font-medium capitalize text-text">{q.resource.replace("_", " ")}</span>
        <span className="tnum text-muted">{q.used} / {q.limit}</span>
      </div>
      <div role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label={`${q.resource} usage`} className="h-1.5 overflow-hidden rounded-full bg-subtle">
        <div className={`h-full rounded-full ${pct >= 90 ? "bg-danger" : pct >= 75 ? "bg-warning" : "bg-primary"}`} style={{ width: `${pct}%` }} />
      </div>
      <Can perm="tenants:manage"><button className="mt-1.5 text-xs font-medium text-primary hover:underline" onClick={() => setOpen(true)}>Change limit</button></Can>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={`Change ${q.resource.replace("_", " ")} limit`} footer={<><DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose><Button onClick={async () => { await put(`/tenants/${tenantId}/quotas`, { resource: q.resource, limit_value: Number(v) }); toast({ title: "Limit updated", tone: "success" }); setOpen(false); onSaved(); }}>Save</Button></>}>
          <Field label="New limit">{(p) => <Input {...p} type="number" min={0} value={v} onChange={(e) => setV(e.target.value)} />}</Field>
        </DialogContent>
      </Dialog>
    </li>
  );
}

function TenantDetail({ tenant, onClose, onChanged }: { tenant: Tenant; onClose: () => void; onChanged: () => void }) {
  const quotas = useApi<Quota[]>(`/tenants/${tenant.id}/quotas`);
  const members = useApi<{ membership_id: string; email: string; name: string; role: string }[]>(`/tenants/${tenant.id}/members`);
  const activity = useApi<{ id: string; action: string; summary: string; created_at: string }[]>(`/tenants/${tenant.id}/activity`);
  const [action, setAction] = React.useState<null | { status: string; title: string; verb: string }>(null);

  const doExport = async () => {
    const data = await post<unknown>(`/tenants/${tenant.id}/export`);
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `tenant-${tenant.slug}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-3">
        <StatusPill status={tenant.status} />
        <div className="ml-auto flex flex-wrap gap-2">
          <Can perm="tenants:manage">
            {tenant.status !== "suspended" && tenant.status !== "cancelled" && <Button size="sm" variant="outline" onClick={() => setAction({ status: "suspended", title: "Suspend this tenant?", verb: "Suspend" })}><Pause className="h-3.5 w-3.5" />Suspend</Button>}
            {tenant.status === "suspended" && <Button size="sm" variant="outline" onClick={() => setAction({ status: "active", title: "Reactivate this tenant?", verb: "Reactivate" })}><Play className="h-3.5 w-3.5" />Reactivate</Button>}
            {tenant.status === "trial" && <Button size="sm" variant="outline" onClick={() => setAction({ status: "active", title: "Activate this tenant?", verb: "Activate" })}><Play className="h-3.5 w-3.5" />Activate</Button>}
            {tenant.status !== "cancelled" && <Button size="sm" variant="danger" onClick={() => setAction({ status: "cancelled", title: "Cancel this tenant?", verb: "Cancel tenant" })}><XCircle className="h-3.5 w-3.5" />Cancel</Button>}
            <Button size="sm" variant="ghost" onClick={doExport}><Download className="h-3.5 w-3.5" />Export</Button>
          </Can>
        </div>
      </div>
      <Tabs defaultValue="overview" className="px-5 pt-2">
        <TabsList className="mb-3 overflow-x-auto">
          <TabsTrigger value="overview">Overview</TabsTrigger><TabsTrigger value="quotas">Quotas</TabsTrigger><TabsTrigger value="members">Members</TabsTrigger><TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="pb-6">
          <DetailList items={[["Name", tenant.name], ["Slug", tenant.slug], ["Contact", tenant.contact_email], ["Plan", tenant.plan_code], ["Created", fmtDate(tenant.created_at)], ["Trial ends", fmtDate(tenant.trial_ends_at)], ["Suspended", fmtDate(tenant.suspended_at)], ["Cancelled", fmtDate(tenant.cancelled_at)], ["Data retained until", fmtDate(tenant.retention_until)]]} />
          <p className="mt-4 rounded-md bg-subtle px-3 py-2 text-xs text-muted">Suspending never deletes data: resources become read-only and can be restored by reactivating. Cancelling starts the retention window, after which an owner-run export/deletion job applies.</p>
        </TabsContent>
        <TabsContent value="quotas" className="pb-6">
          {quotas.loading ? <TableSkeleton rows={4} cols={1} /> : <ul className="divide-y divide-border">{quotas.data?.map((q) => <QuotaRow key={q.resource} q={q} tenantId={tenant.id} onSaved={quotas.reload} />)}</ul>}
        </TabsContent>
        <TabsContent value="members" className="pb-6">
          {members.loading ? <TableSkeleton rows={3} cols={2} /> : !members.data?.length ? <EmptyState title="No members" /> : (
            <ul className="divide-y divide-border">{members.data.map((m) => (<li key={m.membership_id} className="flex items-center justify-between py-2.5 text-sm"><div><p className="font-medium text-text">{m.name}</p><p className="text-xs text-muted">{m.email}</p></div><span className="text-xs text-muted">{m.role}</span></li>))}</ul>
          )}
        </TabsContent>
        <TabsContent value="activity" className="pb-6">
          {activity.loading ? <TableSkeleton rows={4} cols={1} /> : !activity.data?.length ? <EmptyState title="No activity" /> : (
            <ol className="space-y-3 border-l border-border pl-4">{activity.data.map((a) => (<li key={a.id} className="relative text-sm"><span className="absolute top-1.5 -left-[1.3rem] h-2 w-2 rounded-full bg-primary" aria-hidden /><p className="text-text">{a.summary}</p><p className="text-xs text-faint">{fmtDate(a.created_at)}</p></li>))}</ol>
          )}
        </TabsContent>
      </Tabs>
      <FormDialog
        open={!!action}
        onOpenChange={(o) => !o && setAction(null)}
        title={action?.title ?? ""}
        description={action?.status === "cancelled" ? "Cancellation is final. Data is retained for the configured retention period." : "You can reverse this later."}
        fields={[{ name: "reason", label: "Reason", type: "textarea", placeholder: "Visible in the tenant activity log" }]}
        submitLabel={action?.verb ?? "Confirm"}
        destructive={action?.status === "cancelled" || action?.status === "suspended"}
        successMessage="Status updated"
        onSubmit={async (v) => {
          await post(`/tenants/${tenant.id}/status`, { status: action!.status, reason: v.reason });
          onChanged();
          onClose();
        }}
      />
    </div>
  );
}

export function TenantsPage() {
  const toast = useToast();
  const [creating, setCreating] = React.useState(false);
  const [sel, setSel] = React.useState<Tenant | null>(null);
  const [tick, setTick] = React.useState(0);
  return (
    <RequirePerm perm="tenants:read">
      <PageHeader title="Customers" description="Tenant organizations, their lifecycle, quotas and members." />
      <ResourceTable<Tenant>
        key={tick}
        path="/tenants"
        caption="Customers"
        columns={columns}
        filters={filters}
        searchText={(r) => `${r.name} ${r.slug} ${r.contact_email ?? ""}`}
        onCreate={() => setCreating(true)}
        createLabel="New customer"
        rowActions={(r) => (
          <IconButton label={`Open ${r.name}`} onClick={() => setSel(r)}><Eye className="h-4 w-4" /></IconButton>
        )}
      />
      <FormDialog open={creating} onOpenChange={setCreating} title="New customer" description="Creates a tenant with default quotas. Invite the owner from Settings → Team." fields={createFields} submitLabel="Create" onSubmit={async (v) => { await post("/tenants", v); setTick((t) => t + 1); toast({ title: "Customer created", tone: "success" }); }} successMessage="Customer created" />
      <Sheet open={!!sel} onOpenChange={(o) => !o && setSel(null)}>
        <SheetContent title={sel?.name ?? ""} description="Tenant details" className="w-[min(96vw,34rem)]">
          {sel && <TenantDetail tenant={sel} onClose={() => setSel(null)} onChanged={() => { setTick((t) => t + 1); void get("/tenants/current").catch(() => null); }} />}
        </SheetContent>
      </Sheet>
    </RequirePerm>
  );
}
