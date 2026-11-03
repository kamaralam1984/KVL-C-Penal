/* eslint-disable react-hooks/set-state-in-effect -- forms hydrate local state from freshly loaded API data */
"use client";

import * as React from "react";
import { BadgeDollarSign, Download, Gauge, Package, Users } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { ResourceTable } from "@/components/resource/resource-table";
import { FormDialog, type FieldDef } from "@/components/resource/form-dialog";
import { DetailList, RequirePerm, StatusPill, fmtDate, fmtMoney } from "@/components/resource/helpers";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KpiCard } from "@/components/ui/kpi-card";
import { Button, IconButton } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { ConfirmDialog } from "@/components/ui/dialog";
import { EmptyState, ErrorState, PermissionDenied } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { Field, Input } from "@/components/ui/input";
import { useApi } from "@/hooks/use-api";
import { useToast } from "@/components/ui/toast";
import { ApiError, del, patch, post, put } from "@/lib/http";
import type { Column } from "@/components/ui/data-table";
import { Meter, RESOURCES, downloadFile, label } from "./shared";

interface Usage { resource: string; limit: number; used: number }
interface Client { id: string; name: string; status: string; contact_email: string | null; package_id: string | null; package: string | null; retail_price_paise: number | null; renews_at: string | null; notes: string | null; created_at: string; usage: Usage[] }
interface Pkg { id: string; name: string; description: string | null; limits: Record<string, number>; active: boolean; retail_price_paise: number; wholesale_price_paise: number; margin_paise: number; margin_bps: number }
interface Balances { pending_paise: number; approved_paise: number; paid_paise: number; payable_paise: number; total_earned_paise: number }
interface Entry { id: string; kind: string; status: string; amount_paise: number; rate_bps: number | null; base_paise: number | null; created_at: string; approved_at: string | null; paid_at: string | null; payout_reference: string | null; note: string | null }
interface Me { id: string; name: string; status: string; contact_email: string; business: Record<string, string>; limits: { max_clients?: number | null }; clients: number; allocation: Record<string, number>; committed: Record<string, number>; used: Record<string, number>; balances: Balances }

function Loading() {
  return <div className="space-y-3"><Skeleton className="h-24 w-full" /><Skeleton className="h-24 w-full" /></div>;
}

function Gate({ error, children }: { error?: ApiError; children: React.ReactNode }) {
  if (error?.status === 403) return <PermissionDenied description={error.code === "reseller_not_approved" ? "Your reseller account is not approved yet." : "Reseller access is required."} />;
  if (error) return <ErrorState description={error.message} />;
  return <>{children}</>;
}

// ------------------------------------------------------------------------------------------ dashboard
export function ResellerDashboard() {
  const me = useApi<Me>("/reseller/me");
  const sum = useApi<{ clients_total: number; active_packages: number; paid_invoices_ex_tax_paise: number }>("/reseller/reports/summary");
  return (
    <RequirePerm perm="clients:read">
      <PageHeader title="Reseller overview" description="Your clients, packages, platform allocation and commission, loaded live." />
      <Gate error={me.error}>
        {me.loading || !me.data ? <Loading /> : (
          <div className="space-y-4">
            {me.data.status !== "approved" && <p role="status" className="rounded-md bg-warning/10 px-3 py-2 text-sm text-warning">Account status: {me.data.status.replace("_", " ")}. Features unlock after Super Admin approval.</p>}
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <KpiCard label="Clients" value={String(sum.data?.clients_total ?? me.data.clients)} icon={Users} hint={me.data.limits.max_clients != null ? `Limit ${me.data.limits.max_clients}` : undefined} />
              <KpiCard label="Active packages" value={String(sum.data?.active_packages ?? 0)} icon={Package} />
              <KpiCard label="Billed (ex tax)" value={fmtMoney(sum.data?.paid_invoices_ex_tax_paise ?? 0)} icon={Gauge} hint="Paid client invoices" />
              <KpiCard label="Commission payable" value={fmtMoney(me.data.balances.payable_paise)} icon={BadgeDollarSign} hint={`Pending ${fmtMoney(me.data.balances.pending_paise)}`} />
            </div>
            <Card>
              <CardHeader title="Platform allocation" description="Client quotas can never exceed what the platform allocated to you." />
              <CardBody>
                {!Object.keys(me.data.allocation).length ? <EmptyState title="No allocation yet" description="Ask the platform owner to set your resource allocation." /> : (
                  <ul className="grid gap-4 sm:grid-cols-2">
                    {Object.entries(me.data.allocation).map(([r, a]) => (
                      <li key={r}>
                        <div className="mb-1 flex justify-between text-sm"><span className="font-medium capitalize text-text">{label(r)}</span><span className="tnum text-muted">{me.data!.committed[r] ?? 0} committed / {a}</span></div>
                        <Meter name={r} used={me.data!.committed[r] ?? 0} limit={a} />
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>
          </div>
        )}
      </Gate>
    </RequirePerm>
  );
}

// -------------------------------------------------------------------------------------------- clients
export function ResellerClients() {
  const toast = useToast();
  const pk = useApi<{ items: Pkg[] }>("/reseller/packages");
  const [creating, setCreating] = React.useState(false);
  const [sel, setSel] = React.useState<Client | null>(null);
  const [assign, setAssign] = React.useState(false);
  const [tick, setTick] = React.useState(0);
  const pkgOpts = (pk.data?.items ?? []).filter((p) => p.active).map((p) => ({ value: p.id, label: `${p.name} (${fmtMoney(p.retail_price_paise)}/mo)` }));
  const columns: Column<Client>[] = [
    { id: "name", header: "Client", sortValue: (r) => r.name, cell: (r) => <div><p className="font-medium text-text">{r.name}</p><p className="text-xs text-muted">{r.contact_email}</p></div> },
    { id: "status", header: "Status", sortValue: (r) => r.status, cell: (r) => <StatusPill status={r.status} /> },
    { id: "pkg", header: "Package", hideOnMobile: true, cell: (r) => r.package ?? "—" },
    { id: "renews", header: "Renews", hideOnMobile: true, sortValue: (r) => r.renews_at ?? "", cell: (r) => fmtDate(r.renews_at) },
  ];
  const fields: FieldDef[] = [
    { name: "name", label: "Client organization", required: true },
    { name: "contact_email", label: "Client contact email", type: "email", required: true, hint: "An invitation to set up the account is queued for this address." },
    { name: "package_id", label: "Package", type: "select", options: pkgOpts, hint: "Checked against your platform allocation." },
    { name: "notes", label: "Notes", type: "textarea" },
  ];
  return (
    <RequirePerm perm="clients:read">
      <PageHeader title="Clients" description="Customers you manage. You only ever see your own clients." actions={<Button size="sm" variant="outline" onClick={() => void downloadFile("/reseller/reports/clients.csv", "clients.csv")}><Download className="h-4 w-4" />Export CSV</Button>} />
      <ResourceTable<Client> key={tick} path="/reseller/clients" caption="Clients" columns={columns} searchText={(r) => `${r.name} ${r.contact_email ?? ""} ${r.package ?? ""}`} onCreate={() => setCreating(true)} createLabel="New client"
        rowActions={(r) => <Button size="sm" variant="outline" onClick={() => setSel(r)}>Open</Button>} />
      <FormDialog open={creating} onOpenChange={setCreating} title="New client" fields={fields} submitLabel="Create client" successMessage="Client created" onSubmit={async (v) => { await post("/reseller/clients", v); setTick((t) => t + 1); }} />
      <Sheet open={!!sel} onOpenChange={(o) => !o && setSel(null)}>
        <SheetContent title={sel?.name ?? ""} description="Client details, usage and support context" className="w-[min(96vw,32rem)]">
          {sel && <ClientDetail id={sel.id} onAssign={() => setAssign(true)} tick={tick} />}
        </SheetContent>
      </Sheet>
      <FormDialog open={assign} onOpenChange={setAssign} title="Assign package" description="Changing the package re-sets the client's quotas and is checked against your allocation." fields={[{ name: "package_id", label: "Package", type: "select", required: true, options: pkgOpts }]} submitLabel="Assign" successMessage="Package assigned"
        onSubmit={async (v) => { await post(`/reseller/clients/${sel!.id}/package`, v); setTick((t) => t + 1); toast({ title: "Quotas updated", tone: "success" }); }} />
    </RequirePerm>
  );
}

function ClientDetail({ id, onAssign, tick }: { id: string; onAssign: () => void; tick: number }) {
  const ctx = useApi<{ client: Client; open_invoices: number; recent_activity: { action: string; summary: string; created_at: string }[]; tickets: string }>(`/reseller/clients/${id}/support-context`, { t: tick });
  if (ctx.loading) return <div className="p-5"><Loading /></div>;
  if (ctx.error || !ctx.data) return <ErrorState onRetry={ctx.reload} />;
  const { client: c } = ctx.data;
  return (
    <div className="space-y-4 p-5">
      <div className="flex items-center justify-between"><StatusPill status={c.status} /><Button size="sm" variant="outline" onClick={onAssign}>Assign package</Button></div>
      <DetailList items={[["Contact", c.contact_email], ["Package", c.package], ["Retail price", c.retail_price_paise != null ? `${fmtMoney(c.retail_price_paise)}/mo` : null], ["Renews", fmtDate(c.renews_at)], ["Open invoices", ctx.data.open_invoices], ["Created", fmtDate(c.created_at)]]} />
      <div><h3 className="mb-2 text-sm font-semibold text-text">Usage</h3>
        <ul className="space-y-3">{c.usage.map((u) => <li key={u.resource}><div className="mb-1 flex justify-between text-sm"><span className="capitalize">{label(u.resource)}</span><span className="tnum text-muted">{u.used} / {u.limit}</span></div><Meter name={u.resource} used={u.used} limit={u.limit} /></li>)}</ul></div>
      <div><h3 className="mb-2 text-sm font-semibold text-text">Recent activity</h3>
        {!ctx.data.recent_activity.length ? <p className="text-sm text-muted">No activity yet.</p> : <ol className="space-y-2">{ctx.data.recent_activity.map((a, i) => <li key={i} className="text-sm"><p className="text-text">{a.summary}</p><p className="text-xs text-faint">{fmtDate(a.created_at)}</p></li>)}</ol>}</div>
      <p className="rounded-md bg-subtle px-3 py-2 text-xs text-muted">{ctx.data.tickets}</p>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- packages
export function ResellerPackages() {
  const alloc = useApi<{ max_clients: number | null; resources: { resource: string; allocation: number; committed: number; used: number; remaining: number }[] }>("/reseller/allocation");
  const [open, setOpen] = React.useState(false);
  const [rm, setRm] = React.useState<Pkg | null>(null);
  const [tick, setTick] = React.useState(0);
  const toast = useToast();
  const fields: FieldDef[] = [
    { name: "name", label: "Package name", required: true },
    { name: "description", label: "Description" },
    { name: "retail_price", label: "Retail price per month (INR)", type: "number", required: true, hint: "What you charge your client. Margin is shown after saving." },
    ...(alloc.data?.resources ?? []).map((r): FieldDef => ({ name: `lim_${r.resource}`, label: `${label(r.resource)} (max ${r.allocation})`, type: "number", defaultValue: 0 })),
  ];
  const columns: Column<Pkg>[] = [
    { id: "name", header: "Package", sortValue: (r) => r.name, cell: (r) => <div><p className="font-medium text-text">{r.name}</p><p className="text-xs text-muted">{Object.entries(r.limits).map(([k, v]) => `${v} ${label(k)}`).join(", ")}</p></div> },
    { id: "retail", header: "Retail / mo", sortValue: (r) => r.retail_price_paise, align: "right", cell: (r) => <span className="tnum">{fmtMoney(r.retail_price_paise)}</span> },
    { id: "margin", header: "Margin", hideOnMobile: true, align: "right", cell: (r) => <Tooltip content="Retail price minus wholesale cost (platform unit costs x resources)"><span className="tnum">{fmtMoney(r.margin_paise)} ({(r.margin_bps / 100).toFixed(0)}%)</span></Tooltip> },
    { id: "active", header: "Status", cell: (r) => <StatusPill status={r.active ? "active" : "disabled"} /> },
  ];
  return (
    <RequirePerm perm="packages:read">
      <PageHeader title="Packages" description="Sellable packages built from your platform allocation. A package can never exceed that allocation." />
      <Gate error={alloc.error}>
        {alloc.data && (
          <Card className="mb-4"><CardHeader title="Allocation" description={alloc.data.max_clients != null ? `Client limit: ${alloc.data.max_clients}` : undefined} /><CardBody>
            {!alloc.data.resources.length ? <EmptyState title="No allocation" description="The platform owner has not allocated resources to you yet." /> : <ul className="grid gap-4 sm:grid-cols-3">{alloc.data.resources.map((r) => <li key={r.resource}><div className="mb-1 flex justify-between text-sm"><span className="capitalize">{label(r.resource)}</span><span className="tnum text-muted">{r.committed}/{r.allocation}</span></div><Meter name={r.resource} used={r.committed} limit={r.allocation} /></li>)}</ul>}
          </CardBody></Card>
        )}
        <ResourceTable<Pkg> key={tick} path="/reseller/packages" caption="Packages" columns={columns} searchText={(r) => r.name} onCreate={() => setOpen(true)} createLabel="New package" canCreate={!!alloc.data?.resources.length}
          rowActions={(r) => <IconButton label={`Remove ${r.name}`} onClick={() => setRm(r)}>×</IconButton>} />
      </Gate>
      <FormDialog open={open} onOpenChange={setOpen} title="New package" fields={fields} submitLabel="Create package" successMessage="Package created"
        onSubmit={async (v) => {
          const limits: Record<string, number> = {};
          for (const r of RESOURCES) if (Number(v[`lim_${r}`]) > 0) limits[r] = Number(v[`lim_${r}`]);
          await post("/reseller/packages", { name: v.name, description: v.description, limits, retail_price_paise: Math.round(Number(v.retail_price) * 100) });
          setTick((t) => t + 1);
        }} />
      <ConfirmDialog open={!!rm} onOpenChange={(o) => !o && setRm(null)} title={`Remove ${rm?.name}?`} description="Packages in use are deactivated instead of deleted; existing clients keep their quotas." destructive confirmLabel="Remove"
        onConfirm={async () => { await del(`/reseller/packages/${rm!.id}`); toast({ title: "Package removed", tone: "success" }); setTick((t) => t + 1); }} />
    </RequirePerm>
  );
}

// -------------------------------------------------------------------------------------------- branding
interface Domain { id: string; domain: string; state: string; dns_record: { type: string; name: string; value: string }; failure_reason: string | null; last_checked_at: string | null }
export function ResellerBranding() {
  const toast = useToast();
  const b = useApi<{ branding: Record<string, string | null>; domains: Domain[] }>("/reseller/branding");
  const [adding, setAdding] = React.useState(false);
  const [form, setForm] = React.useState<Record<string, string>>({});
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<ApiError>();
  React.useEffect(() => { if (b.data) setForm(Object.fromEntries(Object.entries(b.data.branding).map(([k, v]) => [k, v ?? ""]))); }, [b.data]);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const fe = (n: string) => err?.fields?.find((x) => x.field.endsWith(n))?.message;
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(undefined);
    try {
      const body = Object.fromEntries(["portal_name", "logo_url", "primary_color", "accent_color", "support_email", "email_from_name", "email_footer"].filter((k) => form[k]).map((k) => [k, form[k]]));
      await put("/reseller/branding", body);
      toast({ title: "Branding saved", tone: "success" }); b.reload();
    } catch (x) { setErr(x as ApiError); } finally { setBusy(false); }
  }
  return (
    <RequirePerm perm="branding:read">
      <PageHeader title="White-label branding" description="Your portal name, logo and colours, custom domain, and email branding." />
      <Gate error={b.error}>
        {b.loading || !b.data ? <Loading /> : (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card><CardHeader title="Brand" /><CardBody>
              <form onSubmit={save} className="space-y-3" noValidate>
                <Field label="Portal name" error={fe("portal_name")}>{(p) => <Input {...p} value={form.portal_name ?? ""} onChange={set("portal_name")} maxLength={60} />}</Field>
                <Field label="Logo URL (https)" error={fe("logo_url")}>{(p) => <Input {...p} value={form.logo_url ?? ""} onChange={set("logo_url")} placeholder="https://cdn.example.com/logo.png" />}</Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Primary colour" hint="#RRGGBB" error={fe("primary_color")}>{(p) => <Input {...p} value={form.primary_color ?? ""} onChange={set("primary_color")} placeholder="#1f6feb" />}</Field>
                  <Field label="Accent colour" hint="#RRGGBB" error={fe("accent_color")}>{(p) => <Input {...p} value={form.accent_color ?? ""} onChange={set("accent_color")} placeholder="#c9a227" />}</Field>
                </div>
                <Field label="Support email" error={fe("support_email")}>{(p) => <Input {...p} type="email" value={form.support_email ?? ""} onChange={set("support_email")} />}</Field>
                <Field label="Email sender name" error={fe("email_from_name")}>{(p) => <Input {...p} value={form.email_from_name ?? ""} onChange={set("email_from_name")} />}</Field>
                <Field label="Email footer" error={fe("email_footer")}>{(p) => <Input {...p} value={form.email_footer ?? ""} onChange={set("email_footer")} />}</Field>
                {err && !err.fields?.length && <p role="alert" className="text-sm text-danger">{err.message}</p>}
                <Button type="submit" loading={busy}>Save branding</Button>
              </form>
              <p className="mt-4 rounded-md bg-subtle px-3 py-2 text-xs text-muted">{b.data.branding.disclosure_text} Email branding changes sender name and footer; delivery still uses the platform mail server.</p>
            </CardBody></Card>
            <Card><CardHeader title="Custom domain" description="Prove ownership with a DNS TXT record." actions={<Button size="sm" onClick={() => setAdding(true)}>Add domain</Button>} /><CardBody>
              {!b.data.domains.length ? <EmptyState title="No custom domain" description="Add a domain such as panel.youragency.com." /> : (
                <ul className="space-y-4">{b.data.domains.map((d) => (
                  <li key={d.id} className="rounded-md border border-border p-3 text-sm">
                    <div className="flex items-center justify-between gap-2"><span className="font-medium break-all text-text">{d.domain}</span><StatusPill status={d.state === "verified" ? "verified" : d.state === "failed" ? "failed" : "pending"} /></div>
                    <p className="mt-2 text-xs text-muted">Create this record at your DNS provider:</p>
                    <dl className="mt-1 space-y-1 rounded bg-subtle p-2 font-mono text-xs break-all"><div>{d.dns_record.type} {d.dns_record.name}</div><div>{d.dns_record.value}</div></dl>
                    {d.failure_reason && <p className="mt-2 text-xs text-warning">{d.failure_reason}</p>}
                    <div className="mt-2 flex gap-2"><Button size="sm" variant="outline" onClick={async () => { await post(`/reseller/domains/${d.id}/verify`); b.reload(); }}>Check now</Button><Button size="sm" variant="ghost" onClick={async () => { await del(`/reseller/domains/${d.id}`); b.reload(); }}>Remove</Button></div>
                  </li>))}</ul>)}
              <p className="mt-3 text-xs text-muted">Verification only proves DNS ownership. Pointing the domain at the portal and TLS for it are separate operator steps.</p>
            </CardBody></Card>
          </div>
        )}
      </Gate>
      <FormDialog open={adding} onOpenChange={setAdding} title="Add custom domain" fields={[{ name: "domain", label: "Domain", required: true, placeholder: "panel.example.com" }]} submitLabel="Add domain" successMessage="Domain added" onSubmit={async (v) => { await post("/reseller/domains", v); b.reload(); }} />
    </RequirePerm>
  );
}

// ----------------------------------------------------------------------------------------- commissions
export function ResellerCommissions() {
  const c = useApi<{ balances: Balances; items: Entry[] }>("/reseller/commissions");
  const rec = useApi<{ consistent: boolean; ledger_sum_paise: number; issues: unknown[]; note: string }>("/reseller/commissions/reconciliation");
  const columns: Column<Entry>[] = [
    { id: "date", header: "Date", sortValue: (r) => r.created_at, cell: (r) => fmtDate(r.created_at) },
    { id: "kind", header: "Type", hideOnMobile: true, cell: (r) => r.kind },
    { id: "status", header: "Status", cell: (r) => <StatusPill status={r.status} /> },
    { id: "amt", header: "Amount", align: "right", sortValue: (r) => r.amount_paise, cell: (r) => <span className="tnum">{fmtMoney(r.amount_paise)}</span> },
    { id: "ref", header: "Payout ref", hideOnMobile: true, cell: (r) => r.payout_reference ?? "—" },
  ];
  return (
    <RequirePerm perm="commissions:read">
      <PageHeader title="Commissions" description="Every figure below is computed from ledger entries. Nothing is stored or estimated." actions={<Button size="sm" variant="outline" onClick={() => void downloadFile("/reseller/commissions/export.csv", "commissions.csv")}><Download className="h-4 w-4" />Export CSV</Button>} />
      <Gate error={c.error}>
        {c.data && (
          <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <KpiCard label="Pending" value={fmtMoney(c.data.balances.pending_paise)} hint="Awaiting approval" />
            <KpiCard label="Approved (payable)" value={fmtMoney(c.data.balances.payable_paise)} />
            <KpiCard label="Paid" value={fmtMoney(c.data.balances.paid_paise)} />
            <KpiCard label="Total earned" value={fmtMoney(c.data.balances.total_earned_paise)} hint={rec.data ? (rec.data.consistent ? "Ledger reconciles" : `${rec.data.issues.length} reconciliation issue(s)`) : undefined} />
          </div>
        )}
        <ResourceTable<Entry> path="/reseller/commissions" caption="Ledger entries" columns={columns} searchText={(r) => `${r.kind} ${r.status}`} emptyTitle="No commission yet" emptyDescription="Entries appear when your clients' invoices are paid under a commission rule." />
        {rec.data && <p className="mt-3 text-xs text-muted">{rec.data.note}</p>}
      </Gate>
    </RequirePerm>
  );
}

// ---------------------------------------------------------------------------------------------- reports
export function ResellerReports() {
  const s = useApi<{ clients_by_status: Record<string, number>; clients_total: number; paid_invoices_ex_tax_paise: number; balances: Balances; allocation: Record<string, number>; committed: Record<string, number>; used: Record<string, number> }>("/reseller/reports/summary");
  return (
    <RequirePerm perm="clients:read">
      <PageHeader title="Reports" description="Permitted reports for your own clients, with CSV export." />
      <Gate error={s.error}>
        {s.loading || !s.data ? <Loading /> : (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card><CardHeader title="Clients by status" /><CardBody>{!s.data.clients_total ? <EmptyState title="No clients yet" /> : <DetailList items={Object.entries(s.data.clients_by_status).map(([k, v]) => [k.replace("_", " "), v] as [string, React.ReactNode])} />}</CardBody></Card>
            <Card><CardHeader title="Usage vs allocation" /><CardBody>{!Object.keys(s.data.allocation).length ? <EmptyState title="No allocation" /> : <ul className="space-y-3">{Object.entries(s.data.allocation).map(([r, a]) => <li key={r}><div className="mb-1 flex justify-between text-sm"><span className="capitalize">{label(r)}</span><span className="tnum text-muted">used {s.data!.used[r] ?? 0} / committed {s.data!.committed[r] ?? 0} / allocated {a}</span></div><Meter name={r} used={s.data!.committed[r] ?? 0} limit={a} /></li>)}</ul>}</CardBody></Card>
            <Card className="lg:col-span-2"><CardHeader title="Exports" /><CardBody className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => void downloadFile("/reseller/reports/clients.csv", "clients.csv")}><Download className="h-4 w-4" />Clients CSV</Button>
              <Button variant="outline" onClick={() => void downloadFile("/reseller/commissions/export.csv", "commissions.csv")}><Download className="h-4 w-4" />Commissions CSV</Button>
            </CardBody></Card>
          </div>
        )}
      </Gate>
    </RequirePerm>
  );
}

// --------------------------------------------------------------------------------------------- settings
export function ResellerSettings() {
  const toast = useToast();
  const me = useApi<Me>("/reseller/me");
  const [form, setForm] = React.useState({ name: "", legal_name: "", contact_email: "", gstin: "", phone: "", address: "" });
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<ApiError>();
  React.useEffect(() => { const d = me.data; if (d) setForm({ name: d.name, legal_name: "", contact_email: d.contact_email, gstin: d.business?.gstin ?? "", phone: d.business?.phone ?? "", address: d.business?.address ?? "" }); }, [me.data]);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const fe = (n: string) => err?.fields?.find((x) => x.field.endsWith(n))?.message;
  return (
    <RequirePerm perm="clients:read">
      <PageHeader title="Reseller settings" description="Business profile and account status." />
      <Gate error={me.error}>
        {me.loading || !me.data ? <Loading /> : (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card><CardHeader title="Business details" /><CardBody>
              <form className="space-y-3" noValidate onSubmit={async (e) => { e.preventDefault(); setBusy(true); setErr(undefined); try { await patch("/reseller/me", { name: form.name, contact_email: form.contact_email, business: Object.fromEntries(Object.entries({ gstin: form.gstin, phone: form.phone, address: form.address }).filter(([, v]) => v)) }); toast({ title: "Saved", tone: "success" }); me.reload(); } catch (x) { setErr(x as ApiError); } finally { setBusy(false); } }}>
                <Field label="Business name" error={fe("name")}>{(p) => <Input {...p} value={form.name} onChange={set("name")} />}</Field>
                <Field label="Contact email" error={fe("contact_email")}>{(p) => <Input {...p} type="email" value={form.contact_email} onChange={set("contact_email")} />}</Field>
                <Field label="GSTIN" error={fe("gstin")}>{(p) => <Input {...p} value={form.gstin} onChange={set("gstin")} maxLength={15} />}</Field>
                <Field label="Phone" error={fe("phone")}>{(p) => <Input {...p} value={form.phone} onChange={set("phone")} />}</Field>
                <Field label="Address" error={fe("address")}>{(p) => <Input {...p} value={form.address} onChange={set("address")} />}</Field>
                {err && !err.fields?.length && <p role="alert" className="text-sm text-danger">{err.message}</p>}
                <Button type="submit" loading={busy}>Save</Button>
              </form>
            </CardBody></Card>
            <Card><CardHeader title="Account" /><CardBody><DetailList items={[["Status", <StatusPill key="s" status={me.data.status} />], ["Client limit", me.data.limits.max_clients ?? "No limit"], ["Clients", me.data.clients]]} /></CardBody></Card>
          </div>
        )}
      </Gate>
    </RequirePerm>
  );
}

// ------------------------------------------------------------------------------------------ admin
interface AdminReseller { id: string; name: string; contact_email: string; status: string; applied_at: string; decision_note: string | null; clients: number; allocation: Record<string, number>; committed: Record<string, number>; limits: { max_clients?: number | null; unit_costs?: Record<string, number> }; balances: Balances }

export function AdminResellers() {
  const toast = useToast();
  const [tick, setTick] = React.useState(0);
  const [approve, setApprove] = React.useState<AdminReseller | null>(null);
  const [limits, setLimits] = React.useState<AdminReseller | null>(null);
  const [rule, setRule] = React.useState<AdminReseller | null>(null);
  const [reject, setReject] = React.useState<AdminReseller | null>(null);
  const [suspend, setSuspend] = React.useState<AdminReseller | null>(null);
  const [ledger, setLedger] = React.useState<AdminReseller | null>(null);
  const columns: Column<AdminReseller>[] = [
    { id: "name", header: "Reseller", sortValue: (r) => r.name, cell: (r) => <div><p className="font-medium text-text">{r.name}</p><p className="text-xs text-muted">{r.contact_email}</p></div> },
    { id: "status", header: "Status", sortValue: (r) => r.status, cell: (r) => <StatusPill status={r.status} /> },
    { id: "clients", header: "Clients", hideOnMobile: true, align: "right", sortValue: (r) => r.clients, cell: (r) => <span className="tnum">{r.clients}{r.limits.max_clients != null ? ` / ${r.limits.max_clients}` : ""}</span> },
    { id: "payable", header: "Payable", hideOnMobile: true, align: "right", cell: (r) => <span className="tnum">{fmtMoney(r.balances.payable_paise)}</span> },
    { id: "applied", header: "Applied", hideOnMobile: true, sortValue: (r) => r.applied_at, cell: (r) => fmtDate(r.applied_at) },
  ];
  const allocFields = (r?: AdminReseller | null): FieldDef[] => [
    { name: "max_clients", label: "Max clients", type: "number", defaultValue: r?.limits.max_clients ?? 10 },
    ...RESOURCES.map((k): FieldDef => ({ name: `alloc_${k}`, label: `Allocation: ${label(k)}`, type: "number", defaultValue: r?.allocation?.[k] ?? 0 })),
    ...RESOURCES.map((k): FieldDef => ({ name: `cost_${k}`, label: `Unit cost paise: ${label(k)}`, type: "number", defaultValue: r?.limits.unit_costs?.[k] ?? 0, hint: k === "websites" ? "Wholesale cost per unit per month; used for package margin." : undefined })),
  ];
  const toLimits = (v: Record<string, string | number | boolean>) => ({
    max_clients: v.max_clients === "" || v.max_clients === undefined ? null : Number(v.max_clients),
    allocation: Object.fromEntries(RESOURCES.filter((k) => Number(v[`alloc_${k}`]) > 0).map((k) => [k, Number(v[`alloc_${k}`])])),
    unit_costs: Object.fromEntries(RESOURCES.filter((k) => Number(v[`cost_${k}`]) > 0).map((k) => [k, Number(v[`cost_${k}`])])),
  });
  return (
    <RequirePerm perm="resellers:read">
      <PageHeader title="Resellers" description="Review applications, set allocation and limits, and manage commission." />
      <ResourceTable<AdminReseller> key={tick} path="/resellers" caption="Resellers" columns={columns} searchText={(r) => `${r.name} ${r.contact_email}`} emptyTitle="No reseller applications" emptyDescription="Applications from signed-in users appear here for approval."
        rowActions={(r) => (
          <div className="flex flex-wrap justify-end gap-1.5">
            {r.status === "pending_approval" && <><Button size="sm" onClick={() => setApprove(r)}>Approve</Button><Button size="sm" variant="outline" onClick={() => setReject(r)}>Reject</Button></>}
            {r.status === "approved" && <><Button size="sm" variant="outline" onClick={() => setLimits(r)}>Limits</Button><Button size="sm" variant="outline" onClick={() => setRule(r)}>Commission rule</Button><Button size="sm" variant="outline" onClick={() => setLedger(r)}>Ledger</Button><Button size="sm" variant="ghost" onClick={() => setSuspend(r)}>Suspend</Button></>}
            {r.status === "suspended" && <Button size="sm" variant="outline" onClick={async () => { await post(`/resellers/${r.id}/reactivate`); setTick((t) => t + 1); }}>Reactivate</Button>}
          </div>
        )} />
      <FormDialog open={!!approve} onOpenChange={(o) => !o && setApprove(null)} title={`Approve ${approve?.name}`} description="Grants the reseller role to the applicant. Allocation is the hard ceiling for all their packages and clients." fields={[...allocFields(), { name: "note", label: "Note" }]} submitLabel="Approve" successMessage="Reseller approved"
        onSubmit={async (v) => { await post(`/resellers/${approve!.id}/approve`, { ...toLimits(v), note: v.note }); setTick((t) => t + 1); }} />
      <FormDialog open={!!limits} onOpenChange={(o) => !o && setLimits(null)} title={`Limits for ${limits?.name}`} description="Allocation cannot be lowered below what is already committed to clients." fields={allocFields(limits)} submitLabel="Save limits" successMessage="Limits saved"
        onSubmit={async (v) => { await put(`/resellers/${limits!.id}/limits`, toLimits(v)); setTick((t) => t + 1); }} />
      <FormDialog open={!!reject} onOpenChange={(o) => !o && setReject(null)} title={`Reject ${reject?.name}?`} fields={[{ name: "note", label: "Reason" }]} submitLabel="Reject" destructive successMessage="Application rejected" onSubmit={async (v) => { await post(`/resellers/${reject!.id}/reject`, v); setTick((t) => t + 1); }} />
      <FormDialog open={!!suspend} onOpenChange={(o) => !o && setSuspend(null)} title={`Suspend ${suspend?.name}?`} description="The reseller loses portal access. Their clients are not changed." fields={[{ name: "note", label: "Reason" }]} submitLabel="Suspend" destructive successMessage="Reseller suspended" onSubmit={async (v) => { await post(`/resellers/${suspend!.id}/suspend`, v); setTick((t) => t + 1); }} />
      <FormDialog open={!!rule} onOpenChange={(o) => !o && setRule(null)} title={`Commission rule for ${rule?.name}`} description="Commission = rate x taxable invoice value of the reseller's clients, created when an invoice is verified paid." fields={[{ name: "name", label: "Rule name", required: true, defaultValue: "Standard" }, { name: "rate_percent", label: "Rate (%)", type: "number", required: true, defaultValue: 10 }, { name: "plan_code", label: "Only for plan code (optional)" }]} submitLabel="Add rule" successMessage="Rule added"
        onSubmit={async (v) => { await post("/resellers/commission-rules", { reseller_id: rule!.id, name: v.name, rate_bps: Math.round(Number(v.rate_percent) * 100), plan_code: v.plan_code || null }); }} />
      <Sheet open={!!ledger} onOpenChange={(o) => !o && setLedger(null)}>
        <SheetContent title={`${ledger?.name} ledger`} description="Approve and record payouts" className="w-[min(96vw,34rem)]">{ledger && <AdminLedger r={ledger} onChanged={() => { setTick((t) => t + 1); toast({ title: "Ledger updated", tone: "success" }); }} />}</SheetContent>
      </Sheet>
    </RequirePerm>
  );
}

function AdminLedger({ r, onChanged }: { r: AdminReseller; onChanged: () => void }) {
  const l = useApi<{ balances: Balances; items: Entry[] }>(`/resellers/${r.id}/commissions`);
  const rec = useApi<{ consistent: boolean; issues: { issue: string }[] }>(`/resellers/${r.id}/commissions/reconciliation`);
  const [pay, setPay] = React.useState(false);
  if (l.loading || !l.data) return <div className="p-5"><Loading /></div>;
  const b = l.data.balances;
  return (
    <div className="space-y-4 p-5">
      <DetailList items={[["Pending", fmtMoney(b.pending_paise)], ["Approved (payable)", fmtMoney(b.payable_paise)], ["Paid", fmtMoney(b.paid_paise)], ["Reconciliation", rec.data ? (rec.data.consistent ? "Consistent" : `${rec.data.issues.length} issue(s)`) : "…"]]} />
      <div className="flex gap-2">
        <Button size="sm" variant="outline" disabled={!b.pending_paise && !l.data.items.some((e) => e.status === "pending")} onClick={async () => { await post(`/resellers/${r.id}/commissions/approve`); l.reload(); onChanged(); }}>Approve pending</Button>
        <Button size="sm" disabled={b.payable_paise === 0 && !l.data.items.some((e) => e.status === "approved")} onClick={() => setPay(true)}>Record payout</Button>
      </div>
      <p className="text-xs text-muted">Recording a payout marks approved entries as paid after you transferred the money yourself. This API moves no money. Paid entries are immutable.</p>
      <ul className="divide-y divide-border text-sm">{l.data.items.map((e) => <li key={e.id} className="flex justify-between py-2"><span>{fmtDate(e.created_at)} · {e.kind}</span><span className="tnum">{fmtMoney(e.amount_paise)} <StatusPill status={e.status} /></span></li>)}</ul>
      <FormDialog open={pay} onOpenChange={setPay} title="Record payout" description="Enter the bank/UTR reference of the transfer you made." fields={[{ name: "payout_reference", label: "Payout reference", required: true }]} submitLabel="Mark paid" successMessage="Payout recorded" onSubmit={async (v) => { await post(`/resellers/${r.id}/commissions/pay`, v); l.reload(); onChanged(); }} />
    </div>
  );
}
