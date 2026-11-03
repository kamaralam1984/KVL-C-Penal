"use client";

import * as React from "react";
import { Plus, RefreshCw, Send } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogClose, DialogContent, ConfirmDialog } from "@/components/ui/dialog";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Field, Input } from "@/components/ui/input";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { LiveBadge, StatusPill, fmtDate } from "@/components/resource/helpers";
import { useAuth } from "@/components/providers/auth-provider";
import { useToast } from "@/components/ui/toast";
import { useApi } from "@/hooks/use-api";
import { ApiError, del, patch, post } from "@/lib/http";
import { SecretReveal } from "./secret-reveal";

interface Hook { id: string; url: string; description: string | null; events: string[]; status: string; tenant_id: string | null; rotation_overlap_until: string | null }
interface Delivery { id: string; event_type: string; status: string; attempts: number; last_status_code: number | null; last_error: string | null; next_attempt_at: string | null; created_at: string }
interface Catalog { events: { type: string; description: string }[]; signature: { header: string; format: string } }

export function WebhooksPanel({ tenantScoped = false }: { tenantScoped?: boolean }) {
  const { can } = useAuth();
  const toast = useToast();
  const hooks = useApi<Hook[]>("/webhooks");
  const catalog = useApi<Catalog>("/developer/events");
  const [creating, setCreating] = React.useState(false);
  const [url, setUrl] = React.useState("");
  const [desc, setDesc] = React.useState("");
  const [picked, setPicked] = React.useState<Set<string>>(new Set());
  const [secret, setSecret] = React.useState<string | null>(null);
  const [err, setErr] = React.useState<string>();
  const [busy, setBusy] = React.useState(false);
  const [remove, setRemove] = React.useState<Hook | null>(null);
  const [log, setLog] = React.useState<Hook | null>(null);
  const deliveries = useApi<Delivery[]>(log ? `/webhooks/${log.id}/deliveries` : null);
  const events = (catalog.data?.events ?? []).filter((e) => !(tenantScoped && e.type === "security.alert"));

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(undefined);
    try {
      const h = await post<Hook & { signing_secret: string }>("/webhooks", { url, description: desc || undefined, events: [...picked] });
      setSecret(h.signing_secret);
      setCreating(false);
      setUrl("");
      setDesc("");
      setPicked(new Set());
      hooks.reload();
    } catch (x) {
      setErr(x instanceof ApiError ? x.message : "Could not create the webhook");
    } finally {
      setBusy(false);
    }
  }

  async function act(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      toast({ title: ok, tone: "success" });
      hooks.reload();
      deliveries.reload();
    } catch (x) {
      toast({ title: x instanceof ApiError ? x.message : "Request failed", tone: "danger" });
    }
  }

  return (
    <Card>
      <CardHeader title={tenantScoped ? "Webhooks" : "Webhooks (all endpoints)"} description="HTTPS endpoints only. Requests are signed with HMAC-SHA256 in X-KVL-Signature (t=<unix>,v1=<hex>)."
        actions={<><LiveBadge />{!tenantScoped && can("webhooks:manage") && <Button size="sm" variant="outline" onClick={() => act(() => post("/webhooks/dispatch"), "Dispatch run complete")}>Run deliveries</Button>}<Button size="sm" onClick={() => setCreating(true)}><Plus className="h-4 w-4" /> New webhook</Button></>} />
      <CardBody className="space-y-2">
        {hooks.loading && <p className="text-sm text-muted">Loading…</p>}
        {hooks.error && (hooks.error.status === 403 ? <p className="text-sm text-muted">Your role cannot manage webhooks.</p> : <ErrorState onRetry={hooks.reload} />)}
        {hooks.data?.length === 0 && <EmptyState title="No webhooks" description="Subscribe an HTTPS endpoint to platform events." />}
        {hooks.data?.map((h) => (
          <div key={h.id} className="rounded-lg border border-border p-3 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0"><code className="text-xs break-all text-text">{h.url}</code> <StatusPill status={h.status} />{!h.tenant_id && <Badge tone="primary">platform</Badge>}</div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => act(() => post(`/webhooks/${h.id}/test`), "Test event sent")}><Send className="h-3.5 w-3.5" /> Test</Button>
                <Button size="sm" variant="outline" onClick={() => setLog(h)}>Deliveries</Button>
                <Button size="sm" variant="outline" onClick={async () => { try { const r = await post<{ signing_secret: string }>(`/webhooks/${h.id}/rotate-secret`); setSecret(r.signing_secret); hooks.reload(); } catch { toast({ title: "Could not rotate", tone: "danger" }); } }}><RefreshCw className="h-3.5 w-3.5" /> Rotate secret</Button>
                <Button size="sm" variant="outline" onClick={() => act(() => patch(`/webhooks/${h.id}`, { status: h.status === "active" ? "disabled" : "active" }), "Updated")}>{h.status === "active" ? "Disable" : "Enable"}</Button>
                <Button size="sm" variant="outline" onClick={() => setRemove(h)}>Delete</Button>
              </div>
            </div>
            <p className="mt-1 flex flex-wrap gap-1">{h.events.map((e) => <Badge key={e}>{e}</Badge>)}</p>
            {h.rotation_overlap_until && <p className="mt-1 text-xs text-muted">Previous secret also signs until {fmtDate(h.rotation_overlap_until)}.</p>}
          </div>
        ))}
      </CardBody>

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent title="New webhook" description="Private, loopback and cloud-metadata addresses are rejected, and redirects are never followed.">
          <form onSubmit={create} className="space-y-4">
            <Field label="Endpoint URL" required hint="https only, port 443 or 8443">{(p) => <Input {...p} type="url" value={url} onChange={(e) => setUrl(e.target.value)} required placeholder="https://example.com/hooks/kvl" />}</Field>
            <Field label="Description">{(p) => <Input {...p} value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={200} />}</Field>
            <fieldset>
              <legend className="mb-1 text-sm font-medium text-text">Events</legend>
              <div className="grid max-h-48 gap-1 overflow-y-auto rounded-md border border-border p-2">
                {events.map((ev) => (
                  <label key={ev.type} className="flex min-h-9 items-start gap-2 text-sm text-text">
                    <input type="checkbox" className="mt-1 h-4 w-4 accent-[var(--primary)]" checked={picked.has(ev.type)} onChange={(e) => setPicked((c) => { const n = new Set(c); if (e.target.checked) n.add(ev.type); else n.delete(ev.type); return n; })} />
                    <span><span className="font-medium">{ev.type}</span> <span className="text-muted">{ev.description}</span></span>
                  </label>
                ))}
              </div>
            </fieldset>
            {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err}</p>}
            <div className="flex justify-end gap-2"><DialogClose asChild><Button type="button" variant="outline">Cancel</Button></DialogClose><Button type="submit" loading={busy} disabled={picked.size === 0 || !url}>Create webhook</Button></div>
          </form>
        </DialogContent>
      </Dialog>

      <SecretReveal title="Webhook signing secret" secret={secret} onClose={() => setSecret(null)} />
      <ConfirmDialog open={!!remove} onOpenChange={(o) => !o && setRemove(null)} title="Delete webhook?" description="Pending deliveries are discarded." confirmLabel="Delete" destructive
        onConfirm={() => remove && act(() => del(`/webhooks/${remove.id}`), "Webhook deleted")} />
      <Sheet open={!!log} onOpenChange={(o) => !o && setLog(null)}>
        <SheetContent title="Delivery log" description={log?.url}>
          <div className="space-y-2 p-4">
            {deliveries.data?.length === 0 && <p className="text-sm text-muted">No deliveries yet.</p>}
            {deliveries.data?.map((d) => (
              <div key={d.id} className="rounded-md border border-border p-2 text-sm">
                <div className="flex items-center justify-between gap-2"><span className="font-medium">{d.event_type}</span><StatusPill status={d.status === "dead" ? "failed" : d.status} /></div>
                <p className="text-xs text-muted">{d.attempts} attempt(s){d.last_status_code ? ` · HTTP ${d.last_status_code}` : ""}{d.last_error ? ` · ${d.last_error}` : ""}{d.next_attempt_at && d.status !== "succeeded" ? ` · next ${fmtDate(d.next_attempt_at)}` : ""}</p>
                {(d.status === "dead" || d.status === "retrying") && <Button size="sm" variant="outline" className="mt-1" onClick={() => act(() => post(`/webhooks/deliveries/${d.id}/retry`), "Queued for retry")}>Retry now</Button>}
              </div>
            ))}
          </div>
        </SheetContent>
      </Sheet>
    </Card>
  );
}
