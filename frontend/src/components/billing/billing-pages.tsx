"use client";

import * as React from "react";
import { AlertTriangle, Download, Info, Receipt } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { ResourceTable } from "@/components/resource/resource-table";
import { FormDialog, type FieldDef } from "@/components/resource/form-dialog";
import { DetailList, RequirePerm, StatusPill, fmtDate, fmtMoney } from "@/components/resource/helpers";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KpiCard } from "@/components/ui/kpi-card";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { useApi } from "@/hooks/use-api";
import { useToast } from "@/components/ui/toast";
import { ApiError, api, post, put } from "@/lib/http";
import type { Column } from "@/components/ui/data-table";
import { downloadFile } from "@/components/reseller/shared";
import { CheckoutDialog, openRazorpay } from "./checkout";
import type { Addon, Invoice, Payment, PaymentBlock, Plan, Sub } from "./billing-types";

const Loading = () => <div className="space-y-3"><Skeleton className="h-24 w-full" /><Skeleton className="h-24 w-full" /></div>;
const LIMIT_LABEL = (k: string) => k.replace("_", " ");

function Formula({ text, children }: { text: string; children: React.ReactNode }) {
  return <Tooltip content={text}><span className="inline-flex cursor-help items-center gap-1 underline decoration-dotted underline-offset-4">{children}<Info className="h-3.5 w-3.5 text-faint" aria-label="Formula" /></span></Tooltip>;
}

// -------------------------------------------------------------------------------------- invoices
function InvoiceDetail({ id, admin, onChanged }: { id: string; admin?: boolean; onChanged: () => void }) {
  const toast = useToast();
  const inv = useApi<Invoice>(`/billing/invoices/${id}`);
  const [manual, setManual] = React.useState(false);
  const [voiding, setVoiding] = React.useState(false);
  const [refund, setRefund] = React.useState<Payment | null>(null);
  if (inv.loading) return <div className="p-5"><Loading /></div>;
  if (inv.error || !inv.data) return <ErrorState onRetry={inv.reload} />;
  const i = inv.data;
  async function pay() {
    try {
      const block = await post<PaymentBlock>(`/billing/invoices/${id}/pay`);
      if (block.status === "not_configured") return toast({ title: "Payments not configured", description: "Online payment is not enabled yet.", tone: "warning" });
      if (block.status === "paid") return inv.reload();
      await openRazorpay(block, i, () => { toast({ title: "Waiting for confirmation", description: "The invoice updates once the provider confirms.", tone: "info" }); setTimeout(inv.reload, 4000); }, (r) => toast({ title: "Payment not completed", description: r, tone: "danger" }));
    } catch (e) { toast({ title: "Could not start payment", description: (e as ApiError).message, tone: "danger" }); }
  }
  return (
    <div className="space-y-4 p-5">
      <div className="flex flex-wrap items-center gap-2"><StatusPill status={i.status} />
        <div className="ml-auto flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => void downloadFile(`/billing/invoices/${id}/download`, `invoice-${i.number.replace(/\//g, "-")}.html`, "text/html")}><Download className="h-3.5 w-3.5" />Download</Button>
          {i.status === "open" && !admin && <Button size="sm" onClick={pay}>Pay now</Button>}
          {i.status === "open" && admin && <><Button size="sm" onClick={() => setManual(true)}>Record bank payment</Button><Button size="sm" variant="danger" onClick={() => setVoiding(true)}>Void</Button></>}
        </div></div>
      {i.dunning_attempts > 0 && i.status === "open" && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{i.dunning_attempts} payment attempt(s) failed. Last failure {fmtDate(i.last_failed_at)}. Please retry or use another method.</p>}
      <DetailList items={[["Number", i.number], ["Purpose", i.purpose], ["Issued", fmtDate(i.issued_at)], ["Due", fmtDate(i.due_at)], ["Paid", fmtDate(i.paid_at)], ["Place of supply", i.place_of_supply], ["Buyer GSTIN", i.buyer?.gstin ?? "unregistered"]]} />
      <table className="w-full text-sm"><caption className="sr-only">Invoice lines</caption><thead><tr className="text-left text-xs text-muted"><th className="py-1">Item</th><th className="py-1 text-right">Amount</th></tr></thead>
        <tbody>{i.lines?.map((l, n) => <tr key={n} className="border-t border-border"><td className="py-1.5">{l.description}<span className="block text-xs text-faint">HSN/SAC {l.hsn_sac}</span></td><td className="tnum py-1.5 text-right">{fmtMoney(l.amount_paise)}</td></tr>)}</tbody>
        <tfoot className="tnum text-right">
          <tr className="border-t border-border"><td className="py-1 text-muted">Subtotal</td><td>{fmtMoney(i.subtotal_paise)}</td></tr>
          {i.discount_paise > 0 && <tr><td className="py-1 text-muted">Discount {i.coupon_code}</td><td>-{fmtMoney(i.discount_paise)}</td></tr>}
          {i.igst_paise ? <tr><td className="py-1 text-muted">IGST</td><td>{fmtMoney(i.igst_paise)}</td></tr> : <><tr><td className="py-1 text-muted">CGST</td><td>{fmtMoney(i.cgst_paise)}</td></tr><tr><td className="py-1 text-muted">SGST</td><td>{fmtMoney(i.sgst_paise)}</td></tr></>}
          <tr className="border-t border-border font-semibold"><td className="py-1.5">Total</td><td>{fmtMoney(i.total_paise)}</td></tr>
          {i.refunded_paise > 0 && <tr><td className="py-1 text-muted">Refunded</td><td>-{fmtMoney(i.refunded_paise)}</td></tr>}
        </tfoot></table>
      <div><h3 className="mb-2 text-sm font-semibold text-text">Payments</h3>
        {!i.payments?.length ? <p className="text-sm text-muted">No payment attempts yet.</p> : <ul className="divide-y divide-border text-sm">{i.payments.map((p) => (
          <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 py-2"><span>{fmtDate(p.created_at)} · {p.provider}</span><span className="flex items-center gap-2"><span className="tnum">{fmtMoney(p.amount_paise)}</span><StatusPill status={p.awaiting_confirmation ? "pending" : p.status} />{admin && p.status === "captured" && <Button size="sm" variant="outline" onClick={() => setRefund(p)}>Refund</Button>}</span>
            {p.failure_reason && <span className="w-full text-xs text-danger">{p.failure_reason}</span>}{p.awaiting_confirmation && <span className="w-full text-xs text-muted">Waiting for the provider to confirm this payment.</span>}</li>))}</ul>}
      </div>
      {!!i.credit_notes?.length && <div><h3 className="mb-2 text-sm font-semibold text-text">Credit notes</h3><ul className="text-sm">{i.credit_notes.map((c) => <li key={c.id} className="flex justify-between py-1"><span>{c.number}</span><span className="tnum">{fmtMoney(c.amount_paise)}</span></li>)}</ul></div>}
      <FormDialog open={manual} onOpenChange={setManual} title="Record bank payment" description="Use only after you verified the transfer in your bank. Audited; activates the account." fields={[{ name: "reference", label: "Bank / UTR reference", required: true }]} submitLabel="Mark paid" successMessage="Payment recorded" onSubmit={async (v) => { await post(`/billing/invoices/${id}/manual-payment`, v); inv.reload(); onChanged(); }} />
      <ConfirmDialog open={voiding} onOpenChange={setVoiding} title="Void this invoice?" description="The invoice can no longer be paid. Coupons reserved by it are released." destructive confirmLabel="Void" onConfirm={async () => { await post(`/billing/invoices/${id}/void`); inv.reload(); onChanged(); }} />
      <FormDialog open={!!refund} onOpenChange={(o) => !o && setRefund(null)} title="Refund payment" description={`Refundable up to ${fmtMoney((refund?.amount_paise ?? 0) - (refund?.refunded_paise ?? 0))}. A credit note is issued when the refund is processed.`} fields={[{ name: "amount", label: "Amount (INR)", type: "number", required: true }, { name: "reason", label: "Reason" }]} submitLabel="Refund" destructive successMessage="Refund requested" onSubmit={async (v) => { await post(`/billing/payments/${refund!.id}/refund`, { amount_paise: Math.round(Number(v.amount) * 100), reason: v.reason }); inv.reload(); onChanged(); }} />
    </div>
  );
}

function InvoicesTable({ admin, tick, onChanged }: { admin?: boolean; tick: number; onChanged: () => void }) {
  const [sel, setSel] = React.useState<Invoice | null>(null);
  const columns: Column<Invoice>[] = [
    { id: "n", header: "Invoice", sortValue: (r) => r.number, cell: (r) => <div><p className="font-medium text-text">{r.number}</p><p className="text-xs text-muted">{r.purpose}</p></div> },
    { id: "s", header: "Status", sortValue: (r) => r.status, cell: (r) => <StatusPill status={r.status === "open" && r.dunning_attempts > 0 ? "failed" : r.status} /> },
    { id: "d", header: "Due", hideOnMobile: true, sortValue: (r) => r.due_at ?? "", cell: (r) => fmtDate(r.due_at) },
    { id: "t", header: "Total", align: "right", sortValue: (r) => r.total_paise, cell: (r) => <span className="tnum">{fmtMoney(r.total_paise)}</span> },
  ];
  return (
    <>
      <ResourceTable<Invoice> key={tick} path="/billing/invoices" caption="Invoices" columns={columns} searchText={(r) => `${r.number} ${r.status} ${r.purpose}`} emptyTitle="No invoices yet" emptyDescription="Invoices appear when you subscribe or renew."
        rowActions={(r) => <Button size="sm" variant="outline" onClick={() => setSel(r)}>Open</Button>} />
      <Sheet open={!!sel} onOpenChange={(o) => !o && setSel(null)}>
        <SheetContent title={sel?.number ?? ""} description="Invoice detail" className="w-[min(96vw,34rem)]">{sel && <InvoiceDetail id={sel.id} admin={admin} onChanged={onChanged} />}</SheetContent>
      </Sheet>
    </>
  );
}

function PaymentsTable({ tick }: { tick: number }) {
  const columns: Column<Payment>[] = [
    { id: "d", header: "Date", sortValue: (r) => r.created_at, cell: (r) => fmtDate(r.created_at) },
    { id: "p", header: "Method", hideOnMobile: true, cell: (r) => r.provider },
    { id: "s", header: "Status", cell: (r) => <StatusPill status={r.awaiting_confirmation ? "pending" : r.status} /> },
    { id: "a", header: "Amount", align: "right", sortValue: (r) => r.amount_paise, cell: (r) => <span className="tnum">{fmtMoney(r.amount_paise)}</span> },
  ];
  return <ResourceTable<Payment> key={tick} path="/billing/payments" caption="Payments" columns={columns} searchText={(r) => `${r.provider} ${r.status}`} emptyTitle="No payments yet" />;
}

// ------------------------------------------------------------------------------------- customer
export function CustomerBilling() {
  const toast = useToast();
  const [tick, setTick] = React.useState(0);
  const bump = React.useCallback(() => setTick((t) => t + 1), []);
  const mine = useApi<{ tenant: { name: string; status: string }; subscription: Sub | null }>("/billing/subscription", { t: tick });
  const cat = useApi<{ plans: Plan[]; addons: Addon[] }>("/billing/catalog");
  const cfg = useApi<{ razorpay: { configured: boolean; mode: string | null } }>("/billing/config");
  const [plan, setPlan] = React.useState<Plan | null>(null);
  const [checkout, setCheckout] = React.useState(false);
  const [cancel, setCancel] = React.useState<"end" | "now" | null>(null);
  const [change, setChange] = React.useState<Plan | null>(null);
  const [preview, setPreview] = React.useState<{ mode: string; proration: { net_paise: number } | null; effective_at?: string } | null>(null);
  const sub = mine.data?.subscription ?? null;
  const live = sub && ["trialing", "active", "past_due", "incomplete"].includes(sub.status);

  async function startTrial(p: Plan) {
    try { await post("/billing/subscription/trial", { plan_code: p.code }); toast({ title: "Trial started", tone: "success" }); bump(); }
    catch (e) { toast({ title: "Could not start trial", description: (e as ApiError).message, tone: "danger" }); }
  }
  async function previewChange(p: Plan) {
    setChange(p); setPreview(null);
    try { setPreview(await api(`/billing/subscription/proration-preview`, { query: { plan_code: p.code } })); } catch { setPreview(null); }
  }
  return (
    <RequirePerm perm="billing:read">
      <PageHeader title="Billing" description="Your plan, invoices and payments." />
      {cfg.data && !cfg.data.razorpay.configured && <p role="status" className="mb-4 flex items-start gap-2 rounded-md bg-warning/10 px-3 py-2 text-sm text-warning"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />Payments not configured. Invoices can be created but online payment is unavailable until the operator enables a payment provider.</p>}
      {sub?.status === "past_due" && <p role="alert" className="mb-4 rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">Your renewal payment is overdue. Service continues until {fmtDate(sub.grace_ends_at)}, after which the account is suspended (your data is kept). Pay the open invoice to stay active.</p>}
      {mine.data?.tenant.status === "suspended" && <p role="alert" className="mb-4 rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">This account is suspended. Resources are read-only; paying the open invoice reactivates it.</p>}
      <Tabs defaultValue="overview">
        <TabsList className="mb-4 overflow-x-auto"><TabsTrigger value="overview">Subscription</TabsTrigger><TabsTrigger value="plans">Plans</TabsTrigger><TabsTrigger value="invoices">Invoices</TabsTrigger><TabsTrigger value="payments">Payments</TabsTrigger><TabsTrigger value="profile">Tax details</TabsTrigger></TabsList>
        <TabsContent value="overview">
          {mine.loading ? <Loading /> : mine.error ? <ErrorState onRetry={mine.reload} /> : !sub ? <Card><EmptyState title="No subscription yet" description="Pick a plan to start a trial or subscribe." /></Card> : (
            <Card><CardHeader title={`${sub.plan.name} (v${sub.plan.version})`} description={`Billed ${sub.interval}. Terms are pinned to the version you bought.`} actions={<StatusPill status={sub.status === "trialing" ? "trial" : sub.status} />} /><CardBody>
              <DetailList items={[["Period", `${fmtDate(sub.current_period_start)} → ${fmtDate(sub.current_period_end)}`], ["Trial ends", fmtDate(sub.trial_ends_at)], ["Grace ends", fmtDate(sub.grace_ends_at)], ["Add-ons", sub.addons.map((a) => `${a.code} x${a.qty}`).join(", ") || "None"], ["Limits", Object.entries(sub.terms.limits).map(([k, v]) => `${v} ${LIMIT_LABEL(k)}`).join(", ")], ["Scheduled change", sub.pending_change ? `Downgrade on ${fmtDate(sub.pending_change.effective_at)}` : "None"]]} />
              {sub.cancel_at_period_end && <p className="mt-3 rounded-md bg-warning/10 px-3 py-2 text-sm text-warning">Cancels on {fmtDate(sub.current_period_end)}. Your data is kept afterwards.</p>}
              {live && <div className="mt-4 flex flex-wrap gap-2">
                {sub.cancel_at_period_end ? <Button variant="outline" onClick={async () => { await post("/billing/subscription/resume"); bump(); }}>Keep subscription</Button> : <><Button variant="outline" onClick={() => setCancel("end")}>Cancel at period end</Button><Button variant="ghost" onClick={() => setCancel("now")}>Cancel now</Button></>}
              </div>}
            </CardBody></Card>
          )}
        </TabsContent>
        <TabsContent value="plans">
          {cat.loading ? <Loading /> : !cat.data?.plans.length ? <Card><EmptyState title="No plans available" /></Card> : (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{cat.data.plans.map((p) => (
              <Card key={p.id} className="flex flex-col"><CardHeader title={p.name} description={p.description ?? undefined} /><CardBody className="flex flex-1 flex-col">
                <p className="tnum text-3xl font-semibold text-text">{fmtMoney(p.current_version.price_monthly_paise)}<span className="text-sm font-normal text-muted">/mo</span></p>
                <p className="tnum text-sm text-muted">or {fmtMoney(p.current_version.price_yearly_paise)}/yr · plus GST</p>
                <ul className="my-3 space-y-1 text-sm text-muted">{Object.entries(p.current_version.limits).map(([k, v]) => <li key={k}>{v} {LIMIT_LABEL(k)}</li>)}{Object.entries(p.current_version.features).filter(([, v]) => v).map(([k]) => <li key={k}>{k.replace(/_/g, " ")}</li>)}</ul>
                <div className="mt-auto flex flex-wrap gap-2">
                  {sub && live && sub.plan.code === p.code ? <span className="text-sm font-medium text-success">Current plan</span> : sub?.status === "active" ? <Button variant="outline" onClick={() => void previewChange(p)}>Switch to this plan</Button> : <Button onClick={() => { setPlan(p); setCheckout(true); }}>Subscribe</Button>}
                  {!sub && p.current_version.trial_days > 0 && <Button variant="outline" onClick={() => void startTrial(p)}>Free {p.current_version.trial_days}-day trial</Button>}
                </div>
              </CardBody></Card>))}</div>
          )}
          <CheckoutDialog open={checkout} onOpenChange={setCheckout} plan={plan} addons={cat.data?.addons ?? []} onDone={bump} />
        </TabsContent>
        <TabsContent value="invoices"><InvoicesTable tick={tick} onChanged={bump} /></TabsContent>
        <TabsContent value="payments"><PaymentsTable tick={tick} /></TabsContent>
        <TabsContent value="profile"><TaxProfile /></TabsContent>
      </Tabs>
      <ConfirmDialog open={!!cancel} onOpenChange={(o) => !o && setCancel(null)} title={cancel === "now" ? "Cancel immediately?" : "Cancel at period end?"} description={cancel === "now" ? "Hosting is suspended right away (data is kept). No automatic refund is issued." : "You keep full service until the period ends. Your data is kept."} destructive confirmLabel="Cancel subscription" onConfirm={async () => { await post("/billing/subscription/cancel", { immediate: cancel === "now" }); bump(); }} />
      <ConfirmDialog open={!!change} onOpenChange={(o) => !o && setChange(null)} title={`Switch to ${change?.name}?`}
        description={preview ? preview.mode === "pay_to_apply" ? `Upgrade charge for the rest of this period: ${fmtMoney(preview.proration?.net_paise)} plus GST. The new plan applies once payment is confirmed.` : preview.mode === "scheduled" ? `The change takes effect at the end of the period (${fmtDate(preview.effective_at)}). No refund is given for the current period.` : "The change applies immediately." : "Calculating…"}
        confirmLabel="Confirm change" onConfirm={async () => { const r = await post<{ mode: string; invoice: Invoice | null; payment: PaymentBlock | null }>("/billing/subscription/change", { plan_code: change!.code }); if (r.invoice && r.payment?.status === "order_created") await openRazorpay(r.payment, r.invoice, () => setTimeout(bump, 4000), () => bump()); else if (r.payment?.status === "not_configured") toast({ title: "Payments not configured", description: `Upgrade invoice ${r.invoice?.number} is open; pay it when payments are enabled.`, tone: "warning" }); bump(); }} />
    </RequirePerm>
  );
}

function TaxProfile() {
  const p = useApi<{ legal_name: string | null; gstin: string | null; state_code: string | null; address: string | null }>("/billing/profile");
  const [open, setOpen] = React.useState(false);
  const fields: FieldDef[] = [
    { name: "legal_name", label: "Legal name", defaultValue: p.data?.legal_name ?? "" },
    { name: "gstin", label: "GSTIN", hint: "15 characters; leave empty if unregistered", defaultValue: p.data?.gstin ?? "" },
    { name: "state_code", label: "State code", hint: "Two digits, e.g. 29 for Karnataka. Determines CGST+SGST vs IGST.", defaultValue: p.data?.state_code ?? "" },
    { name: "address", label: "Billing address", defaultValue: p.data?.address ?? "" },
  ];
  return <Card><CardHeader title="Tax details" description="Printed on your GST invoices." actions={<Button size="sm" variant="outline" onClick={() => setOpen(true)}>Edit</Button>} /><CardBody>{p.loading ? <Loading /> : <DetailList items={[["Legal name", p.data?.legal_name], ["GSTIN", p.data?.gstin], ["State code", p.data?.state_code], ["Address", p.data?.address]]} />}</CardBody>
    <FormDialog key={String(p.data?.gstin)} open={open} onOpenChange={setOpen} title="Tax details" fields={fields} successMessage="Saved" onSubmit={async (v) => { await put("/billing/profile", v); p.reload(); }} /></Card>;
}

// ---------------------------------------------------------------------------------------------- admin
export function AdminPlans() {
  const toast = useToast();
  const [tick, setTick] = React.useState(0);
  const data = useApi<{ items: Plan[]; addons: Addon[] }>("/plans", { t: tick });
  const [creating, setCreating] = React.useState(false);
  const [version, setVersion] = React.useState<Plan | null>(null);
  const [addon, setAddon] = React.useState(false);
  const [coupon, setCoupon] = React.useState(false);
  const bump = () => setTick((t) => t + 1);
  const termFields = (v?: Plan["current_version"]): FieldDef[] => [
    { name: "price_monthly", label: "Monthly price (INR, ex GST)", type: "number", required: true, defaultValue: v ? v.price_monthly_paise / 100 : undefined },
    { name: "price_yearly", label: "Yearly price (INR, ex GST)", type: "number", required: true, defaultValue: v ? v.price_yearly_paise / 100 : undefined },
    { name: "websites", label: "Websites", type: "number", defaultValue: v?.limits.websites ?? 5 }, { name: "databases", label: "Databases", type: "number", defaultValue: v?.limits.databases ?? 5 },
    { name: "disk_mb", label: "Disk (MB)", type: "number", defaultValue: v?.limits.disk_mb ?? 10240 }, { name: "bandwidth_gb", label: "Bandwidth (GB)", type: "number", defaultValue: v?.limits.bandwidth_gb ?? 100 },
    { name: "trial_days", label: "Trial days (0 = none)", type: "number", defaultValue: v?.trial_days ?? 0 },
  ];
  const terms = (x: Record<string, string | number | boolean>) => ({
    price_monthly_paise: Math.round(Number(x.price_monthly) * 100), price_yearly_paise: Math.round(Number(x.price_yearly) * 100), trial_days: Number(x.trial_days || 0),
    limits: Object.fromEntries(["websites", "databases", "disk_mb", "bandwidth_gb"].filter((k) => x[k] !== undefined && x[k] !== "").map((k) => [k, Number(x[k])])),
  });
  return (
    <RequirePerm perm="plans:read">
      <PageHeader title="Plans" description="Plans are versioned. Publishing a new version never changes what existing subscribers bought." actions={<><Button size="sm" onClick={() => setCreating(true)}>New plan</Button><Button size="sm" variant="outline" onClick={() => setAddon(true)}>New add-on</Button><Button size="sm" variant="outline" onClick={() => setCoupon(true)}>New coupon</Button></>} />
      {data.loading ? <Loading /> : data.error ? <ErrorState onRetry={data.reload} /> : !data.data?.items.length ? <Card><EmptyState title="No plans yet" description="Create the first plan to start selling hosting." /></Card> : (
        <div className="grid gap-4 lg:grid-cols-2">{data.data.items.map((p) => (
          <Card key={p.id}><CardHeader title={`${p.name} · v${p.current_version.version}`} description={`${p.code} · ${p.subscribers ?? 0} active subscribers`} actions={<StatusPill status={p.active ? "active" : "disabled"} />} /><CardBody>
            <DetailList items={[["Monthly", fmtMoney(p.current_version.price_monthly_paise)], ["Yearly", fmtMoney(p.current_version.price_yearly_paise)], ["Trial", `${p.current_version.trial_days} days`], ["Limits", Object.entries(p.current_version.limits).map(([k, v]) => `${v} ${LIMIT_LABEL(k)}`).join(", ")], ["GST", `${p.current_version.gst_rate_bps / 100}% · SAC ${p.current_version.hsn_sac}`], ["Versions", p.versions?.map((v) => `v${v.version} ${fmtMoney(v.price_monthly_paise)}`).join(" · ")]]} />
            <div className="mt-3 flex gap-2"><Button size="sm" variant="outline" onClick={() => setVersion(p)}>Publish new version</Button><Button size="sm" variant="ghost" onClick={async () => { await api(`/plans/${p.code}`, { method: "PATCH", body: { active: !p.active } }); bump(); }}>{p.active ? "Hide from sign-up" : "Show"}</Button></div>
          </CardBody></Card>))}</div>
      )}
      {!!data.data?.addons.length && <Card className="mt-4"><CardHeader title="Add-ons" /><CardBody><ul className="divide-y divide-border text-sm">{data.data.addons.map((a) => <li key={a.id} className="flex justify-between py-2"><span>{a.name} <span className="text-muted">({a.code})</span></span><span className="tnum">{fmtMoney(a.price_monthly_paise)}/mo</span></li>)}</ul></CardBody></Card>}
      <div className="mt-4"><CouponsTable tick={tick} /></div>
      <FormDialog open={creating} onOpenChange={setCreating} title="New plan" fields={[{ name: "code", label: "Code", required: true, hint: "lowercase letters, numbers, - and _" }, { name: "name", label: "Name", required: true }, { name: "description", label: "Description" }, ...termFields()]} submitLabel="Create plan" successMessage="Plan created" onSubmit={async (v) => { await post("/plans", { code: v.code, name: v.name, description: v.description, ...terms(v) }); bump(); }} />
      <FormDialog key={version?.code} open={!!version} onOpenChange={(o) => !o && setVersion(null)} title={`Publish new version of ${version?.name}`} description="Existing subscriptions keep their current version. New sign-ups get this one." fields={termFields(version?.current_version)} submitLabel="Publish" successMessage="Version published" onSubmit={async (v) => { await post(`/plans/${version!.code}/versions`, terms(v)); bump(); }} />
      <FormDialog open={addon} onOpenChange={setAddon} title="New add-on" fields={[{ name: "code", label: "Code", required: true }, { name: "name", label: "Name", required: true }, { name: "m", label: "Monthly price (INR)", type: "number", required: true }, { name: "y", label: "Yearly price (INR)", type: "number", required: true }, { name: "websites", label: "Extra websites per unit", type: "number", defaultValue: 0 }]} submitLabel="Create" successMessage="Add-on created" onSubmit={async (v) => { await post("/plans/addons", { code: v.code, name: v.name, price_monthly_paise: Math.round(Number(v.m) * 100), price_yearly_paise: Math.round(Number(v.y) * 100), limits: Number(v.websites) > 0 ? { websites: Number(v.websites) } : {} }); bump(); }} />
      <FormDialog open={coupon} onOpenChange={setCoupon} title="New coupon" description="Percent or flat discount, with limits and expiry." fields={[{ name: "code", label: "Code", required: true, hint: "UPPERCASE letters, numbers, - and _" }, { name: "percent", label: "Percent off (%)", type: "number" }, { name: "amount", label: "Or flat off (INR)", type: "number" }, { name: "max_redemptions", label: "Max redemptions", type: "number" }, { name: "valid_until", label: "Expires (YYYY-MM-DD)" }, { name: "plan_codes", label: "Only plans (comma separated codes)" }]} submitLabel="Create coupon" successMessage="Coupon created"
        onSubmit={async (v) => { await post("/billing/coupons", { code: String(v.code).toUpperCase(), percent_bps: v.percent ? Math.round(Number(v.percent) * 100) : undefined, amount_paise: v.amount ? Math.round(Number(v.amount) * 100) : undefined, max_redemptions: v.max_redemptions || undefined, valid_until: v.valid_until ? `${v.valid_until}T23:59:59Z` : undefined, plan_codes: v.plan_codes ? String(v.plan_codes).split(",").map((s) => s.trim()).filter(Boolean) : undefined }); bump(); toast({ title: "Coupon ready", tone: "success" }); }} />
    </RequirePerm>
  );
}

interface CouponRow { id: string; code: string; percent_bps: number | null; amount_paise: number | null; redemptions: number; max_redemptions: number | null; valid_until: string | null; active: boolean }
function CouponsTable({ tick }: { tick: number }) {
  const columns: Column<CouponRow>[] = [
    { id: "c", header: "Coupon", cell: (r) => <span className="font-mono text-sm">{r.code}</span> },
    { id: "v", header: "Value", cell: (r) => (r.percent_bps ? `${r.percent_bps / 100}%` : fmtMoney(r.amount_paise)) },
    { id: "u", header: "Used", hideOnMobile: true, cell: (r) => `${r.redemptions}${r.max_redemptions ? ` / ${r.max_redemptions}` : ""}` },
    { id: "e", header: "Expires", hideOnMobile: true, cell: (r) => fmtDate(r.valid_until) },
    { id: "s", header: "Status", cell: (r) => <StatusPill status={r.active ? "active" : "disabled"} /> },
  ];
  return <ResourceTable<CouponRow> key={tick} path="/billing/coupons" caption="Coupons" columns={columns} searchText={(r) => r.code} emptyTitle="No coupons"
    rowActions={(r, reload) => (r.active ? <Button size="sm" variant="ghost" onClick={async () => { await post(`/billing/coupons/${r.id}/deactivate`); reload(); }}>Deactivate</Button> : null)} />;
}

export function AdminSubscriptions() {
  const [tick, setTick] = React.useState(0);
  const [sel, setSel] = React.useState<Sub | null>(null);
  const toast = useToast();
  const columns: Column<Sub>[] = [
    { id: "t", header: "Customer", sortValue: (r) => r.tenant_name ?? "", cell: (r) => <div><p className="font-medium text-text">{r.tenant_name}</p><p className="text-xs text-muted">{r.plan.name} v{r.plan.version} · {r.interval}</p></div> },
    { id: "s", header: "Status", sortValue: (r) => r.status, cell: (r) => <StatusPill status={r.status === "trialing" ? "trial" : r.status} /> },
    { id: "m", header: "MRR", hideOnMobile: true, align: "right", sortValue: (r) => r.mrr_paise, cell: (r) => <span className="tnum">{fmtMoney(r.mrr_paise)}</span> },
    { id: "e", header: "Period ends", hideOnMobile: true, sortValue: (r) => r.current_period_end ?? "", cell: (r) => fmtDate(r.current_period_end ?? r.trial_ends_at) },
  ];
  return (
    <RequirePerm perm="subscriptions:read">
      <PageHeader title="Subscriptions" description="Every subscription with its pinned plan version." actions={<Button size="sm" variant="outline" onClick={async () => { const r = await post<Record<string, number>>("/subscriptions/lifecycle/run"); toast({ title: "Lifecycle run complete", description: Object.entries(r).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(", "), tone: "success" }); setTick((t) => t + 1); }}>Run lifecycle now</Button>} />
      <ResourceTable<Sub> key={tick} path="/subscriptions" caption="Subscriptions" columns={columns} searchText={(r) => `${r.tenant_name} ${r.plan.name} ${r.status}`} emptyTitle="No subscriptions yet" rowActions={(r) => <Button size="sm" variant="outline" onClick={() => setSel(r)}>History</Button>} />
      <Sheet open={!!sel} onOpenChange={(o) => !o && setSel(null)}><SheetContent title={sel?.tenant_name ?? ""} description="Subscription history" className="w-[min(96vw,30rem)]">{sel && <SubHistory sub={sel} />}</SheetContent></Sheet>
    </RequirePerm>
  );
}

function SubHistory({ sub }: { sub: Sub }) {
  const h = useApi<{ items: { id: string; kind: string; created_at: string }[] }>("/billing/subscription/history", { tenant_id: sub.tenant_id });
  return <div className="space-y-4 p-5"><DetailList items={[["Plan", `${sub.plan.name} v${sub.plan.version}`], ["Interval", sub.interval], ["Status", <StatusPill key="s" status={sub.status} />], ["Period end", fmtDate(sub.current_period_end)], ["Grace ends", fmtDate(sub.grace_ends_at)]]} />
    {h.loading ? <Loading /> : !h.data?.items.length ? <EmptyState title="No history" /> : <ol className="space-y-2 border-l border-border pl-4">{h.data.items.map((e) => <li key={e.id} className="text-sm"><p className="text-text">{e.kind.replace(/_/g, " ")}</p><p className="text-xs text-faint">{fmtDate(e.created_at)}</p></li>)}</ol>}</div>;
}

interface Report { invoices_paid: number; revenue_ex_tax_paise: number; billed_incl_tax_paise: number; refunded_incl_tax_paise: number; failed_payments: number; mrr_paise: number; arr_paise: number; paying_subscriptions: number; churn: { logo_churn_bps: number; revenue_churn_bps: number; opening_subscriptions: number; churned_subscriptions: number }; formulas: Record<string, string>; by_month: { month: string; revenue_ex_tax_paise: number }[] }
export function AdminBilling() {
  const [tick, setTick] = React.useState(0);
  const bump = () => setTick((t) => t + 1);
  const rep = useApi<Report>("/billing/reports/revenue", { t: tick });
  const failed = useApi<{ items: (Invoice & { tenant_name: string })[] }>("/billing/failed-payments", { t: tick });
  const hooks = useApi<{ items: { id: string; event_id: string; event_type: string; status: string; attempts: number; error: string | null; received_at: string }[] }>("/billing/webhook-events", { t: tick });
  const cfg = useApi<{ razorpay: { configured: boolean; webhook_configured: boolean; mode: string | null } }>("/billing/config");
  const [settings, setSettings] = React.useState(false);
  const cur = useApi<Record<string, string | number>>("/billing/settings");
  const f = rep.data?.formulas;
  return (
    <RequirePerm perm="billing:read">
      <PageHeader title="Billing" description="Revenue, recurring revenue, invoices, failed payments and provider events." actions={<Button size="sm" variant="outline" onClick={() => setSettings(true)}>Invoice and GST settings</Button>} />
      {cfg.data && <p role="status" className={`mb-4 rounded-md px-3 py-2 text-sm ${cfg.data.razorpay.configured ? "bg-success/10 text-success" : "bg-warning/10 text-warning"}`}>{cfg.data.razorpay.configured ? `Razorpay connected (${cfg.data.razorpay.mode ?? "unknown"} mode). Webhook secret ${cfg.data.razorpay.webhook_configured ? "set" : "MISSING - payments cannot be confirmed"}.` : "Payments not configured: set the Razorpay keys and webhook secret to accept online payments."}</p>}
      {rep.loading ? <Loading /> : rep.error ? <ErrorState description={rep.error.message} onRetry={rep.reload} /> : rep.data && (
        <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <KpiCard label="Revenue, last 30 days" value={fmtMoney(rep.data.revenue_ex_tax_paise)} hint={`${rep.data.invoices_paid} paid invoices · ex GST`} />
          <KpiCard label="MRR" value={fmtMoney(rep.data.mrr_paise)} hint={f?.mrr} />
          <KpiCard label="ARR" value={fmtMoney(rep.data.arr_paise)} hint="MRR x 12" />
          <KpiCard label="Failed payments" value={String(rep.data.failed_payments)} deltaGoodWhen="down" hint={`${failed.data?.items.length ?? 0} invoices need attention`} />
        </div>
      )}
      {rep.data && <Card className="mb-4"><CardBody className="grid gap-3 text-sm sm:grid-cols-3">
        <div><Formula text={rep.data.formulas.logo_churn}>Logo churn</Formula><p className="tnum text-lg font-semibold">{(rep.data.churn.logo_churn_bps / 100).toFixed(1)}%</p><p className="text-xs text-muted">{rep.data.churn.churned_subscriptions} of {rep.data.churn.opening_subscriptions} subscriptions</p></div>
        <div><Formula text={rep.data.formulas.revenue_churn}>Revenue churn</Formula><p className="tnum text-lg font-semibold">{(rep.data.churn.revenue_churn_bps / 100).toFixed(1)}%</p></div>
        <div><Formula text={rep.data.formulas.revenue}>Refunded (incl. GST)</Formula><p className="tnum text-lg font-semibold">{fmtMoney(rep.data.refunded_incl_tax_paise)}</p></div>
      </CardBody></Card>}
      <Tabs defaultValue="invoices">
        <TabsList className="mb-4 overflow-x-auto"><TabsTrigger value="invoices">Invoices</TabsTrigger><TabsTrigger value="payments">Payments</TabsTrigger><TabsTrigger value="failed">Failed payments</TabsTrigger><TabsTrigger value="events">Provider events</TabsTrigger></TabsList>
        <TabsContent value="invoices"><InvoicesTable admin tick={tick} onChanged={bump} /></TabsContent>
        <TabsContent value="payments"><PaymentsTable tick={tick} /></TabsContent>
        <TabsContent value="failed"><Card><CardBody>{!failed.data?.items.length ? <EmptyState title="No failed payments" description="Open invoices with failed attempts appear here for follow-up." /> : <ul className="divide-y divide-border text-sm">{failed.data.items.map((i) => <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5"><span><span className="font-medium text-text">{i.tenant_name}</span> · {i.number}</span><span className="tnum text-danger">{i.dunning_attempts} failed · {fmtMoney(i.total_paise)}</span></li>)}</ul>}</CardBody></Card></TabsContent>
        <TabsContent value="events"><Card><CardBody>{!hooks.data?.items.length ? <EmptyState title="No provider events yet" /> : <ul className="divide-y divide-border text-sm">{hooks.data.items.map((e) => <li key={e.id} className="flex flex-wrap items-center justify-between gap-2 py-2"><span><span className="font-mono text-xs">{e.event_type}</span><span className="block text-xs text-faint">{fmtDate(e.received_at)} · {e.attempts} attempt(s){e.error ? ` · ${e.error}` : ""}</span></span><StatusPill status={e.status} /></li>)}</ul>}</CardBody></Card></TabsContent>
      </Tabs>
      <FormDialog key={JSON.stringify(cur.data)} open={settings} onOpenChange={setSettings} title="Invoice and GST settings" description="Seller details printed on invoices and the numbering series." successMessage="Settings saved"
        fields={[{ name: "legal_name", label: "Seller legal name", defaultValue: String(cur.data?.legal_name ?? "") }, { name: "gstin", label: "Seller GSTIN", defaultValue: String(cur.data?.gstin ?? "") }, { name: "state_code", label: "Seller state code", defaultValue: String(cur.data?.state_code ?? "") }, { name: "invoice_prefix", label: "Invoice prefix", defaultValue: String(cur.data?.invoice_prefix ?? "INV"), hint: "Numbers are INV/2026-27/000001; the series restarts each financial year (Apr-Mar)." }, { name: "due_days", label: "Payment due (days)", type: "number", defaultValue: Number(cur.data?.due_days ?? 7) }, { name: "grace_days", label: "Grace period (days)", type: "number", defaultValue: Number(cur.data?.grace_days ?? 7) }]}
        onSubmit={async (v) => { await put("/billing/settings", v); cur.reload(); }} />
      <p className="mt-4 flex items-center gap-2 text-xs text-muted"><Receipt className="h-3.5 w-3.5" />All amounts are integer paise; GST is split CGST+SGST (same state) or IGST (different state).</p>
    </RequirePerm>
  );
}
