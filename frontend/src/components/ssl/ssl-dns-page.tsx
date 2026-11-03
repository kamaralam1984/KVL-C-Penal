"use client";

import * as React from "react";
import { Lock, Plus } from "lucide-react";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/shell/page-header";
import { RequirePerm, LiveBadge } from "@/components/resource/helpers";
import { useApi } from "@/hooks/use-api";
import { useAuth } from "@/components/providers/auth-provider";
import { CertSheet, CertTable, ExpiryBadge, RequestCertDialog, SslOverview } from "./certificates";
import { AuditHistory, ChangeHistory, DnsRecords } from "@/components/dns/records";
import { CloudflareCard } from "@/components/dns/cloudflare-card";
import type { Cert, DomainRow } from "./types";

function useDomains() {
  const { me } = useAuth();
  const platform = me?.scope === "platform";
  const d = useApi<{ items: DomainRow[] }>("/ssl/domains");
  return { ...d, platform, items: d.data?.items ?? [] };
}

export function SslDnsPage() {
  const { can } = useAuth();
  const { items, platform, loading, error, reload } = useDomains();
  const [sel, setSel] = React.useState<string>("");
  const [open, setOpen] = React.useState<Cert | null>(null);
  const [req, setReq] = React.useState(false);
  const row = items.find((r) => r.domain === sel) ?? items[0];
  const domain = row?.domain ?? "";
  const q: Record<string, string> = platform && row ? { tenant_id: row.tenant_id } : {};
  const tabQ = q;
  const [tick, setTick] = React.useState(0);
  return (
    <RequirePerm perm="ssl:read">
      <PageHeader title="SSL & DNS" description="Certificates, DNS records and Cloudflare. DNS changes are always previewed and confirmed." actions={<><LiveBadge />{can("ssl:write") && domain && <Button size="sm" onClick={() => setReq(true)}><Plus className="h-4 w-4" /> Request certificate</Button>}</>} />
      <div className="mb-4 flex items-center gap-2">
        <AnimatedIcon icon={Lock} className="text-primary" />
        {loading ? <Skeleton className="h-11 w-60" /> : error ? <ErrorState onRetry={reload} /> : items.length === 0 ? null : (
          <Select aria-label="Domain" className="min-h-11 max-w-xs" value={domain} onChange={(e) => setSel(e.target.value)}>
            {items.map((r) => <option key={r.domain} value={r.domain}>{r.domain}</option>)}
          </Select>
        )}
        {row?.certificate && <ExpiryBadge c={row.certificate} />}
      </div>
      <Tabs defaultValue="certs" className="space-y-4">
        <TabsList className="overflow-x-auto"><TabsTrigger value="certs">Certificates</TabsTrigger><TabsTrigger value="dns">DNS</TabsTrigger><TabsTrigger value="cf">Cloudflare</TabsTrigger><TabsTrigger value="audit">Audit</TabsTrigger></TabsList>
        <TabsContent value="certs"><SslOverview key={tick} q={tabQ} onOpen={setOpen} /></TabsContent>
        <TabsContent value="dns">
          {domain ? <div className="space-y-4"><DnsRecords key={domain} domain={domain} q={q} /><ChangeHistory domain={domain} q={q} /></div> : <Card><EmptyState title="No domains registered" description="Domains appear here once they are registered to the account." /></Card>}
        </TabsContent>
        <TabsContent value="cf">{row || !platform ? <CloudflareCard q={q} /> : <Card><EmptyState title="Select a domain" /></Card>}</TabsContent>
        <TabsContent value="audit"><AuditHistory domain={domain || undefined} q={q} /></TabsContent>
      </Tabs>
      <CertSheet cert={open} onClose={() => setOpen(null)} onChanged={() => setTick((t) => t + 1)} />
      {domain && <RequestCertDialog open={req} onOpenChange={setReq} domain={domain} tenantId={platform ? row?.tenant_id : undefined} onDone={() => { setTick((t) => t + 1); reload(); }} />}
    </RequirePerm>
  );
}

export function CustomerSslPage() {
  const { items, loading, error, reload } = useDomains();
  const [sel, setSel] = React.useState("");
  const [open, setOpen] = React.useState<Cert | null>(null);
  const domain = sel || items[0]?.domain || "";
  const certs = items.map((i) => i.certificate).filter((c): c is Cert => !!c);
  return (
    <RequirePerm perm="ssl:read">
      <PageHeader title="SSL certificates" description="Read-only status of the certificates protecting your domains." actions={<LiveBadge />} />
      <div className="space-y-4">
        <CertTable certs={certs} loading={loading} error={!!error} onRetry={reload} onOpen={setOpen} />
        {!loading && items.some((i) => !i.certificate) && (
          <Card><CardBody><p className="text-sm text-muted">No certificate yet for: {items.filter((i) => !i.certificate).map((i) => i.domain).join(", ")}. Contact support to request one.</p></CardBody></Card>
        )}
        {domain && (
          <>
            <Select aria-label="Domain" className="min-h-11 max-w-xs" value={domain} onChange={(e) => setSel(e.target.value)}>{items.map((r) => <option key={r.domain}>{r.domain}</option>)}</Select>
            <DnsRecords key={domain} domain={domain} q={{}} readOnly />
          </>
        )}
      </div>
      <CertSheet cert={open} onClose={() => setOpen(null)} readOnly onChanged={reload} />
    </RequirePerm>
  );
}
