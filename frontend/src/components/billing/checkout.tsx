/* eslint-disable react-hooks/set-state-in-effect -- polling effect updates state from server responses */
"use client";

import * as React from "react";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Segmented } from "@/components/ui/tabs";
import { Field, Input } from "@/components/ui/input";
import { fmtMoney } from "@/components/resource/helpers";
import { ApiError, api, post } from "@/lib/http";
import { useToast } from "@/components/ui/toast";
import type { Addon, Invoice, PaymentBlock, Plan } from "./billing-types";

declare global {
  interface Window {
    Razorpay?: new (opts: Record<string, unknown>) => { open: () => void; on: (ev: string, cb: (r: unknown) => void) => void };
  }
}

/** Loaded only after the backend reports Razorpay as configured. */
function loadRazorpay(): Promise<boolean> {
  if (typeof window === "undefined") return Promise.resolve(false);
  if (window.Razorpay) return Promise.resolve(true);
  return new Promise((res) => {
    const s = document.createElement("script");
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.async = true;
    s.onload = () => res(true);
    s.onerror = () => res(false);
    document.body.appendChild(s);
  });
}

type Phase = { k: "form" } | { k: "unconfigured"; invoice: Invoice } | { k: "waiting"; invoice: Invoice; timedOut?: boolean } | { k: "paid"; invoice: Invoice } | { k: "failed"; invoice: Invoice; reason: string };

/** Pays an already-created order via Razorpay Checkout. The browser callback only notifies the server; the invoice flips to paid when the signed webhook arrives. */
export async function openRazorpay(block: PaymentBlock, invoice: Invoice, onSubmitted: () => void, onFail: (r: string) => void, prefill?: { email?: string; name?: string }) {
  if (!(await loadRazorpay()) || !window.Razorpay) return onFail("Could not load the payment window.");
  const rz = new window.Razorpay({
    key: block.key_id, order_id: block.order_id, amount: block.amount_paise, currency: "INR", name: "KVL Hosting", description: `Invoice ${invoice.number}`, prefill,
    handler: async (r: { razorpay_payment_id: string; razorpay_order_id: string; razorpay_signature: string }) => {
      try { await post(`/billing/payments/${block.payment_id}/callback`, r); } catch { /* the webhook still decides */ }
      onSubmitted();
    },
    modal: { ondismiss: () => onFail("Payment window closed before completing.") },
  });
  rz.on("payment.failed", (e) => onFail((e as { error?: { description?: string } })?.error?.description ?? "Payment failed."));
  rz.open();
}

export function CheckoutDialog({ open, onOpenChange, plan, addons, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; plan: Plan | null; addons: Addon[]; onDone: () => void }) {
  const toast = useToast();
  const [interval, setInterval_] = React.useState<"monthly" | "yearly">("monthly");
  const [coupon, setCoupon] = React.useState("");
  const [discount, setDiscount] = React.useState<number | null>(null);
  const [couponErr, setCouponErr] = React.useState<string>();
  const [picked, setPicked] = React.useState<Record<string, boolean>>({});
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string>();
  const [phase, setPhase] = React.useState<Phase>({ k: "form" });

  React.useEffect(() => { if (open) { setPhase({ k: "form" }); setDiscount(null); setCoupon(""); setErr(undefined); setCouponErr(undefined); setPicked({}); } }, [open]);

  // poll for the webhook-confirmed state
  React.useEffect(() => {
    if (phase.k !== "waiting") return;
    let n = 0;
    const id = window.setInterval(async () => {
      n++;
      try {
        const inv = await api<Invoice>(`/billing/invoices/${phase.invoice.id}`);
        if (inv.status === "paid") { setPhase({ k: "paid", invoice: inv }); onDone(); window.clearInterval(id); }
      } catch { /* retry */ }
      if (n >= 20) { setPhase((p) => (p.k === "waiting" ? { ...p, timedOut: true } : p)); window.clearInterval(id); }
    }, 3000);
    return () => window.clearInterval(id);
  }, [phase, onDone]);

  if (!plan) return null;
  const price = interval === "yearly" ? plan.current_version.price_yearly_paise : plan.current_version.price_monthly_paise;
  const addonTotal = addons.filter((a) => picked[a.code]).reduce((s, a) => s + (interval === "yearly" ? a.price_yearly_paise : a.price_monthly_paise), 0);
  const subtotal = price + addonTotal;

  async function checkCoupon() {
    setCouponErr(undefined);
    try { const r = await post<{ discount_paise: number }>("/billing/coupons/validate", { code: coupon, plan_code: plan!.code, interval }); setDiscount(r.discount_paise); }
    catch (e) { setDiscount(null); setCouponErr(e instanceof ApiError ? e.message : "Could not check the coupon."); }
  }
  async function start() {
    setBusy(true); setErr(undefined);
    try {
      const r = await post<{ invoice: Invoice; payment: PaymentBlock }>("/billing/checkout", { plan_code: plan!.code, interval, coupon_code: coupon || undefined, addons: addons.filter((a) => picked[a.code]).map((a) => ({ code: a.code, qty: 1 })) });
      if (r.payment.status === "paid") { setPhase({ k: "paid", invoice: r.invoice }); onDone(); return; }
      if (r.payment.status === "not_configured") { setPhase({ k: "unconfigured", invoice: r.invoice }); onDone(); return; }
      await openRazorpay(r.payment, r.invoice, () => setPhase({ k: "waiting", invoice: r.invoice }), (reason) => { setPhase({ k: "failed", invoice: r.invoice, reason }); toast({ title: "Payment not completed", description: reason, tone: "danger" }); });
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Checkout failed."); } finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={`Subscribe to ${plan.name}`} description="You are charged only after the payment provider confirms it.">
        {phase.k === "form" && (
          <div className="space-y-4">
            <Segmented label="Billing interval" value={interval} onChange={(v) => { setInterval_(v); setDiscount(null); }} options={[{ value: "monthly", label: "Monthly" }, { value: "yearly", label: "Yearly" }]} />
            {addons.length > 0 && (
              <fieldset className="space-y-2"><legend className="text-sm font-medium text-text">Add-ons</legend>
                {addons.map((a) => <label key={a.code} className="flex items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" checked={!!picked[a.code]} onChange={(e) => setPicked((p) => ({ ...p, [a.code]: e.target.checked }))} />{a.name} <span className="text-muted">+{fmtMoney(interval === "yearly" ? a.price_yearly_paise : a.price_monthly_paise)}</span></label>)}
              </fieldset>
            )}
            <Field label="Coupon code" error={couponErr} hint={discount != null ? `Saves ${fmtMoney(discount)} before tax` : undefined}>
              {(p) => <div className="flex gap-2"><Input {...p} value={coupon} onChange={(e) => { setCoupon(e.target.value.toUpperCase()); setDiscount(null); }} placeholder="Optional" /><Button type="button" variant="outline" disabled={!coupon} onClick={checkCoupon}>Apply</Button></div>}
            </Field>
            <p className="tnum text-sm text-muted">Subtotal {fmtMoney(subtotal)}{discount ? ` - discount ${fmtMoney(discount)}` : ""} + GST (shown on the invoice)</p>
            {plan.current_version.trial_days > 0 && <p className="text-xs text-muted">Paying now starts the paid period immediately. A free {plan.current_version.trial_days}-day trial is available from the plan card.</p>}
            {err && <p role="alert" className="text-sm text-danger">{err}</p>}
            <Button className="w-full" loading={busy} onClick={start}>Continue to payment</Button>
          </div>
        )}
        {phase.k === "unconfigured" && (
          <div role="status" className="space-y-3 text-center">
            <AlertTriangle className="mx-auto h-8 w-8 text-warning" aria-hidden />
            <h3 className="text-lg font-semibold text-text">Payments not configured</h3>
            <p className="text-sm text-muted">Online payments are not enabled on this installation yet. Invoice {phase.invoice.number} was created and stays unpaid. Your account is not activated until payment is verified; contact support to pay by bank transfer.</p>
            <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
          </div>
        )}
        {phase.k === "waiting" && (
          <div role="status" className="space-y-3 text-center">
            <Loader2 className="mx-auto h-8 w-8 animate-spin text-primary" aria-hidden />
            <h3 className="text-lg font-semibold text-text">Waiting for payment confirmation</h3>
            <p className="text-sm text-muted">{phase.timedOut ? "Confirmation is taking longer than usual. You can close this window; the invoice updates automatically once the provider confirms." : "Your bank has responded. We are waiting for the payment provider to confirm it to our servers."}</p>
            <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
          </div>
        )}
        {phase.k === "paid" && (
          <div role="status" className="space-y-3 text-center">
            <CheckCircle2 className="mx-auto h-8 w-8 text-success" aria-hidden />
            <h3 className="text-lg font-semibold text-text">Payment confirmed</h3>
            <p className="text-sm text-muted">Invoice {phase.invoice.number} is paid.</p>
            <Button onClick={() => onOpenChange(false)}>Done</Button>
          </div>
        )}
        {phase.k === "failed" && (
          <div role="alert" className="space-y-3 text-center">
            <AlertTriangle className="mx-auto h-8 w-8 text-danger" aria-hidden />
            <h3 className="text-lg font-semibold text-text">Payment not completed</h3>
            <p className="text-sm text-muted">{phase.reason} Invoice {phase.invoice.number} is still open; you can retry from Invoices.</p>
            <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
