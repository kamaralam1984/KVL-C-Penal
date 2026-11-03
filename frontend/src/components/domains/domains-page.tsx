"use client";

import * as React from "react";
import { Check, Copy, Globe2, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { ResourceTable } from "@/components/resource/resource-table";
import { FormDialog, type FieldDef } from "@/components/resource/form-dialog";
import { Can, DetailList, RequirePerm, StatusPill, fmtDate } from "@/components/resource/helpers";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Button, IconButton } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import { Field, Input, Select } from "@/components/ui/input";
import { useApi } from "@/hooks/use-api";
import { useToast } from "@/components/ui/toast";
import { ApiError, del, patch, post } from "@/lib/http";
import type { Column, FilterDef } from "@/components/ui/data-table";

export interface DnsChallenge { type: string; name: string; value: string; ttl: number }
export interface Domain {
  id: string; tenant_id: string; website_id: string | null; hostname: string; unicode_name: string | null; kind: string;
  verification_state: string; verified_at: string | null; verified_via: string | null; last_checked_at: string | null; last_error: string | null;
  expires_at: string | null; registrar: string | null; dns_challenge: DnsChallenge | null; created_at: string;
}
interface SiteLite { id: string; name: string }

export function CopyField({ label, value }: { label: string; value: string }) {
  const [done, setDone] = React.useState(false);
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-muted">{label}</p>
      <div className="flex items-center gap-2 rounded-md border border-border bg-subtle px-2.5 py-1.5">
        <code className="min-w-0 flex-1 break-all text-xs text-text">{value}</code>
        <IconButton
          label={`Copy ${label}`}
          onClick={() => {
            void navigator.clipboard?.writeText(value).then(() => {
              setDone(true);
              setTimeout(() => setDone(false), 1500);
            });
          }}
        >
          {done ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
        </IconButton>
      </div>
    </div>
  );
}

/** DNS instructions + verify button; shared by the domain drawer and the website detail. */
export function DnsInstructions({ domain, onChanged }: { domain: Domain; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = React.useState(false);
  const resolver = useApi<{ available: boolean; message: string | null }>("/domains/resolver");
  async function verify() {
    setBusy(true);
    try {
      const d = await post<Domain>(`/domains/${domain.id}/verify`);
      toast({ title: d.verification_state === "verified" ? "Domain verified" : "Not verified yet", description: d.last_error ?? undefined, tone: d.verification_state === "verified" ? "success" : "info" });
      onChanged();
    } catch (e) {
      toast({ title: "Verification failed", description: e instanceof ApiError ? e.message : "Network error", tone: "danger" });
    } finally {
      setBusy(false);
    }
  }
  if (domain.verification_state === "verified")
    return (
      <p className="flex items-center gap-2 rounded-md bg-success/10 px-3 py-2 text-sm text-success">
        <ShieldCheck className="h-4 w-4" /> Ownership verified {domain.verified_via === "parent_domain" ? "through its verified parent domain" : "via DNS TXT"} on {fmtDate(domain.verified_at)}.
      </p>
    );
  return (
    <div className="space-y-3 rounded-lg border border-border p-3">
      <p className="text-sm text-text">Add this TXT record at your DNS provider to prove you own <strong>{domain.hostname}</strong>. We never change your DNS.</p>
      {domain.dns_challenge && (
        <div className="space-y-2">
          <div className="flex gap-4 text-xs text-muted"><span>Type: <strong className="text-text">{domain.dns_challenge.type}</strong></span><span>TTL: <strong className="text-text">{domain.dns_challenge.ttl}</strong></span></div>
          <CopyField label="Name / Host" value={domain.dns_challenge.name} />
          <CopyField label="Value" value={domain.dns_challenge.value} />
        </div>
      )}
      {domain.verification_state === "unavailable" && <p role="status" className="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">DNS lookups are unavailable right now: {domain.last_error}</p>}
      {resolver.data && !resolver.data.available && <p role="status" className="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">{resolver.data.message}</p>}
      {domain.verification_state !== "unavailable" && domain.last_error && <p className="text-xs text-muted">Last check {fmtDate(domain.last_checked_at)}: {domain.last_error}</p>}
      <Can perm="domains:write">
        <Button size="sm" loading={busy} onClick={verify}><RefreshCw className="h-3.5 w-3.5" /> Verify now</Button>
      </Can>
    </div>
  );
}

function DomainDetail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const d = useApi<Domain>(`/domains/${id}`);
  const sites = useApi<{ items: SiteLite[] }>("/websites", { limit: 100 });
  const [confirmDel, setConfirmDel] = React.useState(false);
  const [expires, setExpires] = React.useState("");
  const [registrar, setRegistrar] = React.useState("");
  React.useEffect(() => {
    if (d.data) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setExpires(d.data.expires_at ? d.data.expires_at.slice(0, 10) : "");
      setRegistrar(d.data.registrar ?? "");
    }
  }, [d.data]);
  const dom = d.data;
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      toast({ title: ok, tone: "success" });
      d.reload();
      onChanged();
    } catch (e) {
      toast({ title: "Action failed", description: e instanceof ApiError ? e.message : "Network error", tone: "danger" });
    }
  };
  if (!dom) return <p className="p-5 text-sm text-muted">{d.error ? d.error.message : "Loading…"}</p>;
  return (
    <div className="space-y-5 p-5">
      <div className="flex items-center gap-2"><StatusPill status={dom.verification_state} /><span className="text-xs text-muted capitalize">{dom.kind}</span></div>
      <DnsInstructions domain={dom} onChanged={() => { d.reload(); onChanged(); }} />
      <DetailList items={[["Hostname", dom.hostname], ["Unicode name", dom.unicode_name], ["Added", fmtDate(dom.created_at)], ["Last checked", fmtDate(dom.last_checked_at)]]} />
      <Can perm="domains:write">
        <div className="space-y-3">
          <Field label="Mapped website" hint="Only verified domains are included in the website's Nginx configuration.">
            {(p) => (
              <Select {...p} value={dom.website_id ?? ""} onChange={(e) => void act(() => (e.target.value ? patch(`/domains/${id}`, { website_id: e.target.value }) : patch(`/domains/${id}`, { unmap: true })), "Mapping updated")}>
                <option value="">Not mapped</option>
                {sites.data?.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Registrar (optional)">{(p) => <Input {...p} value={registrar} maxLength={120} onChange={(e) => setRegistrar(e.target.value)} />}</Field>
          <Field label="Expiry date (optional)" hint="Metadata you enter; the panel does not query registries.">{(p) => <Input {...p} type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />}</Field>
          <Button size="sm" variant="outline" onClick={() => void act(() => patch(`/domains/${id}`, { registrar, ...(expires ? { expires_at: `${expires}T00:00:00Z` } : {}) }), "Metadata saved")}>Save details</Button>
        </div>
        <div className="border-t border-border pt-4">
          <Button size="sm" variant="danger" onClick={() => setConfirmDel(true)}><Trash2 className="h-3.5 w-3.5" /> Remove domain</Button>
        </div>
      </Can>
      <ConfirmDialog open={confirmDel} onOpenChange={setConfirmDel} destructive title={`Remove ${dom.hostname}?`} description="The hostname is released and can be claimed again. Live websites must be re-provisioned first." confirmLabel="Remove"
        onConfirm={() => void act(async () => { await del(`/domains/${id}`); onClose(); }, "Domain removed")} />
    </div>
  );
}

const columns: Column<Domain>[] = [
  { id: "host", header: "Domain", sortValue: (r) => r.hostname, cell: (r) => (<div className="min-w-0"><p className="font-medium break-all text-text">{r.hostname}</p>{r.unicode_name && <p className="text-xs text-muted">{r.unicode_name}</p>}</div>) },
  { id: "state", header: "Ownership", sortValue: (r) => r.verification_state, cell: (r) => <StatusPill status={r.verification_state} /> },
  { id: "kind", header: "Type", hideOnMobile: true, cell: (r) => <span className="capitalize">{r.kind}</span> },
  { id: "mapped", header: "Mapped", hideOnMobile: true, cell: (r) => (r.website_id ? "Yes" : "No") },
  { id: "exp", header: "Expires", hideOnMobile: true, sortValue: (r) => r.expires_at ?? "", cell: (r) => fmtDate(r.expires_at) },
];
const filters: FilterDef<Domain>[] = [{ id: "state", label: "Ownership", options: ["pending", "verified", "failed", "unavailable"], match: (r, v) => r.verification_state === v }];

export function DomainsPage() {
  const [tick, setTick] = React.useState(0);
  const [creating, setCreating] = React.useState(false);
  const [sel, setSel] = React.useState<string | null>(null);
  const sites = useApi<{ items: SiteLite[] }>("/websites", { limit: 100 });
  const toast = useToast();
  const fields = React.useMemo<FieldDef[]>(() => [
    { name: "hostname", label: "Domain name", required: true, placeholder: "shop.example.com", hint: "Letters, digits, hyphens and dots. International names are converted automatically. No wildcards or ports." },
    { name: "kind", label: "Type", type: "select", required: true, defaultValue: "primary", options: [{ value: "primary", label: "Primary" }, { value: "alias", label: "Alias" }, { value: "subdomain", label: "Subdomain" }] },
    { name: "website_id", label: "Website (optional)", type: "select", options: (sites.data?.items ?? []).map((s) => ({ value: s.id, label: s.name })) },
  ], [sites.data]);
  return (
    <RequirePerm perm="domains:read">
      <PageHeader title="Domains" description="Add domains, prove ownership with a DNS TXT record and map them to websites." actions={<AnimatedIcon icon={Globe2} idle="float" className="text-primary" size={26} />} />
      <ResourceTable<Domain>
        key={tick}
        path="/domains"
        caption="Domains"
        columns={columns}
        filters={filters}
        searchText={(r) => `${r.hostname} ${r.unicode_name ?? ""}`}
        onCreate={() => setCreating(true)}
        createLabel="Add domain"
        emptyDescription="Add your first domain to get DNS verification instructions."
        rowActions={(r) => <Button size="sm" variant="outline" onClick={() => setSel(r.id)}>Open</Button>}
      />
      <FormDialog open={creating} onOpenChange={setCreating} title="Add domain" description="You will get a TXT record to add at your DNS provider next." fields={fields} submitLabel="Add domain" successMessage="Domain added"
        onSubmit={async (v) => { const d = await post<Domain>("/domains", v); setTick((t) => t + 1); setSel(d.id); toast({ title: "Add the TXT record to verify" , tone: "info"}); }} />
      <Sheet open={!!sel} onOpenChange={(o) => !o && setSel(null)}>
        <SheetContent title="Domain" description="Verification, mapping and metadata" className="w-[min(96vw,30rem)]">
          {sel && <DomainDetail key={sel} id={sel} onClose={() => setSel(null)} onChanged={() => setTick((t) => t + 1)} />}
        </SheetContent>
      </Sheet>
    </RequirePerm>
  );
}
