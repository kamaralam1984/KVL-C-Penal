"use client";

import * as React from "react";
import { Plus, RefreshCw } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogClose, DialogContent, ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { LiveBadge, StatusPill, fmtDate } from "@/components/resource/helpers";
import { useToast } from "@/components/ui/toast";
import { useApi } from "@/hooks/use-api";
import { ApiError, del, post } from "@/lib/http";
import { SecretReveal } from "./secret-reveal";

interface Key { id: string; name: string; prefix: string; scopes: string[]; tenant_id: string | null; status: string; expires_at: string | null; last_used_at: string | null; last_used_ip: string | null; rate_limit_per_minute: number; request_count: number; owner_email?: string }
interface Scope { scope: string; grantable: boolean }
interface Created extends Key { key: string }

/** API keys for the signed-in user (tenantScoped) or every key on the platform (admin view; needs apikeys:manage). */
export function ApiKeysPanel({ tenantScoped = false }: { tenantScoped?: boolean }) {
  const toast = useToast();
  const keys = useApi<Key[]>("/apikeys");
  const scopes = useApi<Scope[]>("/developer/scopes");
  const [creating, setCreating] = React.useState(false);
  const [secret, setSecret] = React.useState<string | null>(null);
  const [revoke, setRevoke] = React.useState<Key | null>(null);
  const [rotate, setRotate] = React.useState<Key | null>(null);
  const [name, setName] = React.useState("");
  const [days, setDays] = React.useState("90");
  const [rate, setRate] = React.useState("60");
  const [picked, setPicked] = React.useState<Set<string>>(new Set());
  const [err, setErr] = React.useState<string>();
  const [busy, setBusy] = React.useState(false);
  const grantable = (scopes.data ?? []).filter((s) => s.grantable);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(undefined);
    try {
      const k = await post<Created>("/apikeys", { name, scopes: [...picked], expires_in_days: Number(days), rate_limit_per_minute: Number(rate) });
      setSecret(k.key);
      setCreating(false);
      setName("");
      setPicked(new Set());
      keys.reload();
    } catch (x) {
      setErr(x instanceof ApiError ? x.message : "Could not create the key");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={tenantScoped ? "API keys" : "API keys (all users)"}
        description="Keys act with your permissions, narrowed to the scopes you pick. Send as Authorization: Bearer kvl_…"
        actions={<><LiveBadge /><Button size="sm" onClick={() => setCreating(true)}><Plus className="h-4 w-4" /> New key</Button></>}
      />
      <CardBody className="space-y-2">
        {keys.loading && <p className="text-sm text-muted">Loading…</p>}
        {keys.error && (keys.error.status === 403 ? <p className="text-sm text-muted">Your role cannot manage API keys.</p> : <ErrorState onRetry={keys.reload} />)}
        {keys.data?.length === 0 && <EmptyState title="No API keys" description="Create a key to call the API from scripts or CI." />}
        {keys.data?.map((k) => (
          <div key={k.id} className="rounded-lg border border-border p-3 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0"><span className="font-medium text-text">{k.name}</span> <code className="text-xs text-muted">{k.prefix}…</code> <StatusPill status={k.status} /></div>
              {k.status === "active" && (
                <div className="flex gap-2">
                  {!k.owner_email && <Button size="sm" variant="outline" onClick={() => setRotate(k)}><RefreshCw className="h-3.5 w-3.5" /> Rotate</Button>}
                  <Button size="sm" variant="outline" onClick={() => setRevoke(k)}>Revoke</Button>
                </div>
              )}
            </div>
            <p className="mt-1 flex flex-wrap gap-1">{k.scopes.map((s) => <Badge key={s}>{s}</Badge>)}</p>
            <p className="mt-1 text-xs text-muted">
              {k.owner_email && <>Owner {k.owner_email} · </>}Expires {fmtDate(k.expires_at)} · last used {fmtDate(k.last_used_at)}{k.last_used_ip ? ` from ${k.last_used_ip}` : ""} · {k.request_count} requests · {k.rate_limit_per_minute}/min
            </p>
          </div>
        ))}
      </CardBody>

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent title="New API key" description="Scopes are limited to permissions you hold. The secret is shown once.">
          <form onSubmit={create} className="space-y-4">
            <Field label="Name" required>{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} />}</Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Expires in (days)">{(p) => <Input {...p} type="number" min={1} max={365} value={days} onChange={(e) => setDays(e.target.value)} />}</Field>
              <Field label="Rate limit / minute">{(p) => <Input {...p} type="number" min={1} max={600} value={rate} onChange={(e) => setRate(e.target.value)} />}</Field>
            </div>
            <fieldset>
              <legend className="mb-1 text-sm font-medium text-text">Scopes</legend>
              <div className="grid max-h-48 grid-cols-1 gap-1 overflow-y-auto rounded-md border border-border p-2 sm:grid-cols-2">
                {grantable.map((s) => (
                  <label key={s.scope} className="flex min-h-9 items-center gap-2 text-sm text-text">
                    <input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" checked={picked.has(s.scope)} onChange={(e) => setPicked((c) => { const n = new Set(c); if (e.target.checked) n.add(s.scope); else n.delete(s.scope); return n; })} />
                    {s.scope}
                  </label>
                ))}
              </div>
            </fieldset>
            {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err}</p>}
            <div className="flex justify-end gap-2"><DialogClose asChild><Button type="button" variant="outline">Cancel</Button></DialogClose><Button type="submit" loading={busy} disabled={picked.size === 0 || !name.trim()}>Create key</Button></div>
          </form>
        </DialogContent>
      </Dialog>

      <SecretReveal title="Your new API key" secret={secret} onClose={() => setSecret(null)} />
      <ConfirmDialog open={!!revoke} onOpenChange={(o) => !o && setRevoke(null)} title="Revoke API key?" description={`"${revoke?.name}" stops working immediately. This cannot be undone.`} confirmLabel="Revoke" destructive
        onConfirm={async () => { if (revoke) { await del(`/apikeys/${revoke.id}`); toast({ title: "Key revoked", tone: "success" }); keys.reload(); } }} />
      <ConfirmDialog open={!!rotate} onOpenChange={(o) => !o && setRotate(null)} title="Rotate API key?" description="A new secret is issued and the current key is revoked immediately. Update your integration right away." confirmLabel="Rotate"
        onConfirm={async () => { if (rotate) { const k = await post<Created>(`/apikeys/${rotate.id}/rotate`); setSecret(k.key); keys.reload(); } }} />
    </Card>
  );
}
