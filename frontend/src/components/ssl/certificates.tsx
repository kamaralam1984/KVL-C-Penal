"use client";

import * as React from "react";
import { CheckCircle2, RefreshCw, XCircle } from "lucide-react";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { DataTable, type Column } from "@/components/ui/data-table";
import { KpiCard } from "@/components/ui/kpi-card";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { DetailList, StatusPill, fmtDate } from "@/components/resource/helpers";
import { useApi, useMutation } from "@/hooks/use-api";
import { api } from "@/lib/http";
import { useAuth } from "@/components/providers/auth-provider";
import type { Cert, CertDetail, Overview, Prereq } from "./types";

const SEV: Record<Cert["severity"], Tone> = { ok: "success", notice: "info", warning: "warning", critical: "danger" };

export function ExpiryBadge({ c }: { c: Cert }) {
  const label = c.status === "expired" ? "Expired" : c.days_remaining == null ? c.status : `${c.days_remaining}d left`;
  return (
    <Badge tone={SEV[c.severity]} dot>
      {label}
      {!c.trusted && c.status === "issued" ? " · staging" : ""}
    </Badge>
  );
}

export function CertTable({ certs, loading, error, onRetry, onOpen }: { certs: Cert[]; loading: boolean; error: boolean; onRetry: () => void; onOpen?: (c: Cert) => void }) {
  const cols: Column<Cert>[] = [
    { id: "domain", header: "Domain", sortValue: (c) => c.domain, cell: (c) => (onOpen ? <button className="min-h-11 text-left font-medium text-primary hover:underline md:min-h-0" onClick={() => onOpen(c)}>{c.domain}</button> : <span className="font-medium">{c.domain}</span>) },
    { id: "status", header: "Status", sortValue: (c) => c.status, cell: (c) => <StatusPill status={c.status} /> },
    { id: "exp", header: "Expiry", sortValue: (c) => c.days_remaining ?? -1, cell: (c) => <ExpiryBadge c={c} /> },
    { id: "env", header: "Environment", hideOnMobile: true, cell: (c) => (c.environment === "production" ? "Production" : "Staging (untrusted)") },
    { id: "until", header: "Valid until", hideOnMobile: true, sortValue: (c) => c.not_after ?? "", cell: (c) => <span className="tnum">{fmtDate(c.not_after)}</span> },
  ];
  const rows = [...certs].sort((a, b) => (a.days_remaining ?? 9999) - (b.days_remaining ?? 9999)).map((c) => ({ ...c }));
  return <DataTable caption="Certificates" rows={rows} columns={cols} searchText={(c) => `${c.domain} ${c.status}`} loading={loading} error={error} onRetry={onRetry} emptyTitle="No certificates yet" emptyDescription="Request a certificate for one of your domains." />;
}

export function SslOverview({ q, onOpen }: { q: Record<string, string>; onOpen: (c: Cert) => void }) {
  const { data, error, loading, reload } = useApi<Overview>("/ssl/overview", q);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard label="Certificates" value={String(data?.total ?? "—")} hint={`${data?.counts?.issued ?? 0} issued`} />
        <KpiCard label="Expiring in 7 days" value={String(data?.expiring_7d ?? "—")} deltaGoodWhen="down" />
        <KpiCard label="Expiring in 30 days" value={String(data?.expiring_30d ?? "—")} deltaGoodWhen="down" />
        <KpiCard label="Failed / expired" value={String((data?.counts?.failed ?? 0) + (data?.counts?.expired ?? 0))} hint={data?.staging_only ? `${data.staging_only} staging only` : undefined} />
      </div>
      <CertTable certs={data?.certificates ?? []} loading={loading} error={!!error} onRetry={reload} onOpen={onOpen} />
    </div>
  );
}

export function RequestCertDialog({ open, onOpenChange, domain, tenantId, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; domain: string; tenantId?: string; onDone: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const [production, setProduction] = React.useState(false);
  const pre = useApi<Prereq>(open && domain ? "/ssl/prerequisites" : null, { domain, tenant_id: tenantId });
  const m = useMutation(() => api("/ssl/certificates", { method: "POST", body: { domain, production, tenant_id: tenantId } }));
  async function go() {
    const r = await m.run(undefined);
    if (r) {
      toast({ title: "Certificate requested", tone: "success" });
      onDone();
      onOpenChange(false);
    }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={`Request certificate for ${domain}`} description="We check prerequisites first. Requests use the Let's Encrypt staging environment unless you choose production.">
        {pre.loading ? <Skeleton className="h-24 w-full" /> : pre.error ? <ErrorState onRetry={pre.reload} /> : (
          <ul className="space-y-2 text-sm">
            {pre.data?.checks.map((c) => (
              <li key={c.check} className="flex items-start gap-2">
                {c.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" /> : <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />}
                <span><span className="font-medium">{c.check.replace(/_/g, " ")}</span> — <span className="text-muted">{c.message}</span></span>
              </li>
            ))}
          </ul>
        )}
        {can("ssl:manage") && (
          <label className="mt-4 flex min-h-11 items-center gap-2 text-sm">
            <input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" checked={production} onChange={(e) => setProduction(e.target.checked)} />
            Issue a trusted production certificate (counts toward Let&apos;s Encrypt rate limits)
          </label>
        )}
        {!production && <p className="mt-3 text-xs text-muted">Staging certificates are not trusted by browsers; use them to test the flow.</p>}
        {m.error && <p role="alert" className="mt-3 rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{m.error.message}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={go} loading={m.pending} disabled={!pre.data?.ok}>Request {production ? "production" : "staging"} certificate</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function CertSheet({ cert, onClose, readOnly, onChanged }: { cert: Cert | null; onClose: () => void; readOnly?: boolean; onChanged: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const d = useApi<CertDetail>(cert ? `/ssl/certificates/${cert.id}` : null);
  const renew = useMutation(() => api(`/ssl/certificates/${cert!.id}/renew`, { method: "POST", body: {} }));
  const auto = useMutation((v: boolean) => api(`/ssl/certificates/${cert!.id}`, { method: "PATCH", body: { auto_renew: v } }));
  const c = d.data ?? cert;
  const canWrite = !readOnly && can("ssl:write") && (c?.environment !== "production" || can("ssl:manage"));
  return (
    <Sheet open={!!cert} onOpenChange={(o) => !o && onClose()}>
      {cert && c && (
        <SheetContent title={c.domain} description="Certificate metadata. Private keys are never shown.">
          <div className="space-y-4 p-5">
            <div className="flex flex-wrap items-center gap-2"><StatusPill status={c.status} /><ExpiryBadge c={c} /></div>
            <DetailList items={[["Environment", c.environment === "production" ? "Production" : "Staging (not trusted)"], ["Issuer", c.issuer], ["Valid until", fmtDate(c.not_after)], ["Serial", c.serial], ["SHA-256", <span key="f" className="font-mono text-xs break-all">{c.fingerprint_sha256}</span>], ["Names", c.sans?.join(", ")], ["Last renewed", fmtDate(c.last_renewed_at)], ["Renewal failures", c.renewal_failures]]} />
            {c.last_error && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{c.last_error}</p>}
            {canWrite && (
              <div className="space-y-2">
                <label className="flex min-h-11 items-center gap-2 text-sm">
                  <input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" checked={c.auto_renew} disabled={auto.pending} onChange={async (e) => { if (await auto.run(e.target.checked)) { d.reload(); onChanged(); } }} />
                  Renew automatically
                </label>
                <Button variant="outline" loading={renew.pending} onClick={async () => { if (await renew.run(undefined)) { toast({ title: "Renewal started", tone: "success" }); d.reload(); onChanged(); } }}>
                  <RefreshCw className="h-4 w-4" /> Renew now
                </Button>
                {(renew.error || auto.error) && <p role="alert" className="text-sm text-danger">{(renew.error ?? auto.error)?.message}</p>}
              </div>
            )}
            <div>
              <h3 className="mb-2 text-sm font-semibold">History</h3>
              {d.loading ? <Skeleton className="h-16 w-full" /> : (d.data?.history?.length ?? 0) === 0 ? <p className="text-sm text-muted">No events yet.</p> : (
                <ul className="divide-y divide-border text-sm">
                  {d.data!.history.map((h, i) => <li key={i} className="flex justify-between gap-3 py-2"><span>{h.action}</span><span className="tnum text-muted">{fmtDate(h.at)}</span></li>)}
                </ul>
              )}
            </div>
          </div>
        </SheetContent>
      )}
    </Sheet>
  );
}

export { Card, CardBody, CardHeader };
