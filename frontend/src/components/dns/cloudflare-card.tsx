"use client";

import * as React from "react";
import { Cloud, Unplug } from "lucide-react";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { ErrorState, PermissionDenied } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { DetailList, StatusPill, fmtDate } from "@/components/resource/helpers";
import { useApi, useMutation } from "@/hooks/use-api";
import { api } from "@/lib/http";
import { useAuth } from "@/components/providers/auth-provider";
import type { Connection } from "@/components/ssl/types";

export function CloudflareCard({ q }: { q: Record<string, string> }) {
  const { can } = useAuth();
  const toast = useToast();
  const { data, error, loading, reload } = useApi<Connection>("/cloudflare/connection", q);
  const [token, setToken] = React.useState("");
  const [label, setLabel] = React.useState("");
  const [confirmDel, setConfirmDel] = React.useState(false);
  const owner = can("dns:manage");
  const connect = useMutation(() => api("/cloudflare/connection", { method: "PUT", body: { token, label: label || undefined, tenant_id: q.tenant_id } }));
  const check = useMutation(() => api(`/cloudflare/connection/check${q.tenant_id ? `?tenant_id=${q.tenant_id}` : ""}`, { method: "POST", body: {} }));
  const err = connect.error ?? check.error;
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const r = await connect.run(undefined);
    setToken(""); // write-only: never kept after submit
    if (r) { toast({ title: "Cloudflare connected", tone: "success" }); setLabel(""); }
    reload();
  }
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><AnimatedIcon icon={Cloud} />Cloudflare connection</span>} description="Scoped API token only. Global API keys are rejected." />
      <CardBody className="space-y-4">
        {loading ? <Skeleton className="h-24 w-full" /> : error ? <ErrorState onRetry={reload} /> : data && (
          <>
            <div className="flex flex-wrap items-center gap-2"><StatusPill status={data.status} />{data.fingerprint && <span className="text-xs text-muted">token fingerprint {data.fingerprint}</span>}</div>
            {data.status === "not_configured" && <p className="text-sm text-muted">Not configured. DNS records cannot be read or changed until a token is connected.</p>}
            {data.status !== "connected" && data.status !== "not_configured" && <p role="alert" className="rounded-md bg-warning/10 px-3 py-2 text-sm text-warning">{data.last_error ?? `Connection status: ${data.status}`}</p>}
            {data.configured && <DetailList items={[["Label", data.label], ["Last checked", fmtDate(data.last_checked_at)], ["Zones", data.zones.length]]} />}
            {data.zones.length > 0 && <ul className="flex flex-wrap gap-2 text-sm">{data.zones.map((z) => <li key={z.id} className="rounded-md bg-subtle px-2 py-1">{z.name}{z.status ? ` · ${z.status}` : ""}</li>)}</ul>}
            <div className="text-xs text-muted">Required token permissions: {data.required_permissions.join(", ")}.</div>
            {owner ? (
              <form onSubmit={submit} className="space-y-3" noValidate>
                <Field label="API token" hint="Stored encrypted. Never shown again." error={connect.error?.fields?.[0]?.message}>{(p) => <Input {...p} type="password" autoComplete="new-password" value={token} onChange={(e) => setToken(e.target.value)} />}</Field>
                <Field label="Label (optional)">{(p) => <Input {...p} value={label} onChange={(e) => setLabel(e.target.value)} />}</Field>
                {err && !err.fields?.length && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err.message}</p>}
                <div className="flex flex-wrap gap-2">
                  <Button type="submit" loading={connect.pending} disabled={token.length < 20}>{data.configured ? "Replace token" : "Connect"}</Button>
                  {data.configured && <>
                    <Button type="button" variant="outline" loading={check.pending} onClick={async () => { await check.run(undefined); reload(); }}>Re-check</Button>
                    <Button type="button" variant="outline" onClick={() => setConfirmDel(true)}><Unplug className="h-4 w-4" /> Disconnect</Button>
                  </>}
                </div>
              </form>
            ) : <PermissionDenied description="Only platform owners can connect or change the Cloudflare token." />}
          </>
        )}
      </CardBody>
      <ConfirmDialog open={confirmDel} onOpenChange={setConfirmDel} destructive title="Disconnect Cloudflare?" description="The encrypted token is deleted. DNS records can no longer be synced or changed until you reconnect." confirmLabel="Disconnect"
        onConfirm={async () => { await api(`/cloudflare/connection${q.tenant_id ? `?tenant_id=${q.tenant_id}` : ""}`, { method: "DELETE" }).catch(() => undefined); reload(); }} />
    </Card>
  );
}
