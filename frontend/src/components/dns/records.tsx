"use client";

import * as React from "react";
import { Pencil, Plus, RefreshCw, Trash2, Undo2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { StatusPill, fmtDate } from "@/components/resource/helpers";
import { useApi, useMutation } from "@/hooks/use-api";
import { ApiError, api } from "@/lib/http";
import { useAuth } from "@/components/providers/auth-provider";
import type { AuditItem, Change, DnsRec, RecordsResp } from "@/components/ssl/types";

const TYPES = ["A", "AAAA", "CNAME", "TXT", "MX", "NS", "CAA", "SRV"];
const show = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));

export function DnsStatusNote({ status }: { status: string }) {
  if (status === "connected") return null;
  const msg: Record<string, string> = {
    not_configured: "Cloudflare is not connected for this account, so DNS records cannot be read or changed here. Connect it in the Cloudflare tab.",
    zone_not_authorised: "The connected Cloudflare token does not cover this domain.",
    permission_denied: "The Cloudflare token lacks required permissions. Reconnect with a corrected token.",
    invalid: "The Cloudflare token is invalid. Reconnect with a new token.",
    rate_limited: "Cloudflare is rate limiting requests. Try again shortly.",
  };
  return <p role="status" className="mb-3 rounded-md bg-warning/10 px-3 py-2 text-sm text-warning">{msg[status] ?? `Cloudflare status: ${status}`}</p>;
}

export function DnsRecords({ domain, q, readOnly }: { domain: string; q: Record<string, string>; readOnly?: boolean }) {
  const { can } = useAuth();
  const toast = useToast();
  const { data, error, loading, reload } = useApi<RecordsResp>(`/dns/${encodeURIComponent(domain)}/records`, q);
  const [editing, setEditing] = React.useState<{ rec?: DnsRec; action: "create" | "update" | "delete" } | null>(null);
  const [change, setChange] = React.useState<Change | null>(null);
  const sync = useMutation(() => api(`/dns/${encodeURIComponent(domain)}/sync`, { method: "POST", body: {}, query: undefined }));
  const canEdit = !readOnly && can("dns:write");
  const cols: Column<DnsRec>[] = [
    { id: "type", header: "Type", sortValue: (r) => r.type, cell: (r) => <Badge tone="primary">{r.type}</Badge> },
    { id: "name", header: "Name", sortValue: (r) => r.name, cell: (r) => <span className="break-all">{r.name}</span> },
    { id: "content", header: "Value", cell: (r) => <span className="break-all font-mono text-xs">{r.priority != null ? `${r.priority} ` : ""}{r.content}</span> },
    { id: "ttl", header: "TTL", hideOnMobile: true, sortValue: (r) => r.ttl, cell: (r) => <span className="tnum">{r.ttl === 1 ? "auto" : r.ttl}</span> },
    { id: "proxy", header: "Proxy", hideOnMobile: true, cell: (r) => (r.proxied == null ? "—" : r.proxied ? <Badge tone="warning" dot>Proxied</Badge> : <Badge dot>DNS only</Badge>) },
  ];
  return (
    <Card>
      <CardHeader title="DNS records" description={data?.synced_at ? `Synced ${fmtDate(data.synced_at)}` : "Not synced yet"} actions={
        canEdit && (
          <>
            <Button variant="outline" size="sm" loading={sync.pending} onClick={async () => { const qs = q.tenant_id ? `?tenant_id=${q.tenant_id}` : ""; if (await api(`/dns/${encodeURIComponent(domain)}/sync${qs}`, { method: "POST", body: {} }).then(() => true, (e: ApiError) => { toast({ title: e.message, tone: "danger" }); return false; })) { toast({ title: "Records synced", tone: "success" }); reload(); } }}>
              <RefreshCw className="h-4 w-4" /> Sync
            </Button>
            <Button size="sm" onClick={() => setEditing({ action: "create" })}><Plus className="h-4 w-4" /> Add</Button>
          </>
        )
      } />
      <CardBody>
        {data && <DnsStatusNote status={data.status} />}
        {error ? <ErrorState onRetry={reload} /> : (
          <DataTable caption="DNS records" rows={data?.records ?? []} columns={cols} loading={loading} searchText={(r) => `${r.type} ${r.name} ${r.content}`}
            emptyTitle="No records mirrored" emptyDescription="Sync from Cloudflare to load this zone's records."
            rowActions={canEdit ? (r) => (
              <div className="flex gap-1">
                <Button variant="ghost" size="sm" aria-label={`Edit ${r.name}`} onClick={() => setEditing({ rec: r, action: "update" })}><Pencil className="h-4 w-4" /></Button>
                <Button variant="ghost" size="sm" aria-label={`Delete ${r.name}`} onClick={() => setEditing({ rec: r, action: "delete" })}><Trash2 className="h-4 w-4 text-danger" /></Button>
              </div>
            ) : undefined} />
        )}
        {!readOnly && !canEdit && <p className="mt-3 text-xs text-muted">You can view DNS records but not request changes.</p>}
      </CardBody>
      {editing && <RecordEditor domain={domain} q={q} state={editing} onClose={() => setEditing(null)} onPreview={(c) => { setEditing(null); setChange(c); }} />}
      <ChangeDialog change={change} domain={domain} q={q} onClose={() => setChange(null)} onApplied={() => reload()} onReplace={setChange} />
    </Card>
  );
}

function RecordEditor({ domain, q, state, onClose, onPreview }: { domain: string; q: Record<string, string>; state: { rec?: DnsRec; action: "create" | "update" | "delete" }; onClose: () => void; onPreview: (c: Change) => void }) {
  const r = state.rec;
  const [f, setF] = React.useState({ type: r?.type ?? "A", name: r?.name ?? "", content: r?.content ?? "", ttl: String(r?.ttl ?? 1), priority: r?.priority != null ? String(r.priority) : "", proxied: !!r?.proxied });
  const [ack, setAck] = React.useState(false);
  const m = useMutation(() => api<Change>(`/dns/${encodeURIComponent(domain)}/changes`, { method: "POST", body: {
    action: state.action, record_id: r?.id, protected_ack: ack, tenant_id: q.tenant_id,
    record: state.action === "delete" ? undefined : { type: f.type, name: f.name || "@", content: f.content, ttl: Number(f.ttl) || 1, priority: f.priority === "" ? null : Number(f.priority), proxied: ["A", "AAAA", "CNAME"].includes(f.type) ? f.proxied : null },
  } }));
  const err = m.error;
  const fe = (n: string) => err?.fields?.find((x) => x.field === n || x.field.endsWith(`.${n}`))?.message;
  const set = (k: string, v: string | boolean) => setF((s) => ({ ...s, [k]: v }));
  const title = state.action === "create" ? "Add DNS record" : state.action === "update" ? "Edit DNS record" : "Delete DNS record";
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const c = await m.run(undefined);
    if (c) onPreview(c);
  }
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={title} description="Nothing is changed yet. You will review a before/after preview first.">
        <form onSubmit={submit} className="space-y-3" noValidate>
          {state.action !== "delete" ? (
            <>
              <Field label="Type" error={fe("type")}>{(p) => <Select {...p} value={f.type} disabled={state.action === "update"} onChange={(e) => set("type", e.target.value)}>{TYPES.map((t) => <option key={t}>{t}</option>)}</Select>}</Field>
              <Field label="Name" hint="Use @ for the domain itself" error={fe("name")}>{(p) => <Input {...p} value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="www" />}</Field>
              <Field label="Value" error={fe("content")}>{(p) => <Input {...p} value={f.content} onChange={(e) => set("content", e.target.value)} />}</Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="TTL (1 = auto)" error={fe("ttl")}>{(p) => <Input {...p} inputMode="numeric" value={f.ttl} onChange={(e) => set("ttl", e.target.value)} />}</Field>
                {["MX", "SRV"].includes(f.type) && <Field label="Priority" error={fe("priority")}>{(p) => <Input {...p} inputMode="numeric" value={f.priority} onChange={(e) => set("priority", e.target.value)} />}</Field>}
              </div>
              {["A", "AAAA", "CNAME"].includes(f.type) && <label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" checked={f.proxied} onChange={(e) => set("proxied", e.target.checked)} /> Proxy through Cloudflare</label>}
            </>
          ) : <p className="text-sm">Preview deleting <strong>{r?.type} {r?.name}</strong>.</p>}
          {err?.code === "protected_record" && (
            <label className="flex items-start gap-2 rounded-md bg-warning/10 px-3 py-2 text-sm text-warning"><input type="checkbox" className="mt-0.5 h-4 w-4" checked={ack} onChange={(e) => setAck(e.target.checked)} /> {err.message} I understand and want to continue.</label>
          )}
          {err && err.code !== "protected_record" && !err.fields?.length && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err.message}</p>}
          {err?.fields?.length ? <p role="alert" className="text-sm text-danger">{err.message}</p> : null}
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" loading={m.pending}>Preview change</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Rec({ r }: { r: Change["before"] }) {
  if (!r) return <span className="text-muted">—</span>;
  return <span className="font-mono text-xs break-all">{r.type} {r.name} → {r.priority != null ? `${r.priority} ` : ""}{r.content} (TTL {r.ttl === 1 ? "auto" : r.ttl}{r.proxied ? ", proxied" : ""})</span>;
}

type CdProps = { domain: string; q?: Record<string, string>; onClose: () => void; onApplied: () => void; onReplace: (c: Change) => void };

export function ChangeDialog({ change, ...rest }: CdProps & { change: Change | null }) {
  return change ? <ChangeDialogInner key={change.id} change={change} {...rest} /> : null;
}

function ChangeDialogInner({ change, domain, onClose, onApplied, onReplace }: CdProps & { change: Change }) {
  const { can } = useAuth();
  const toast = useToast();
  const [typed, setTyped] = React.useState("");
  const [prod, setProd] = React.useState(false);
  const [local, setLocal] = React.useState<Change>(change);
  const c = local;
  const ownerOk = can("dns:manage");
  const confirm = useMutation(() => api<Change>(`/dns/changes/${c!.id}/confirm`, { method: "POST", body: { confirm: true, confirm_production: true } }));
  const verify = useMutation(() => api<Change>(`/dns/changes/${c!.id}/verify`, { method: "POST", body: {} }));
  const rollback = useMutation(() => api<Change>(`/dns/changes/${c!.id}/rollback`, { method: "POST", body: {} }));
  const discard = useMutation(() => api(`/dns/changes/${c!.id}/reject`, { method: "POST", body: {} }));
  const err = confirm.error ?? verify.error ?? rollback.error ?? discard.error;
  const ready = ownerOk && prod && typed.trim().toLowerCase() === domain.toLowerCase();
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto" title={`DNS change: ${c.action} (${c.status})`} description={`${c.domain} · ${c.unchanged_record_count ?? "other"} other records will not be touched`}>
        <div className="space-y-3 text-sm">
          <div className="space-y-1"><p className="font-medium">Before</p><Rec r={c.before} /><p className="pt-1 font-medium">After</p><Rec r={c.after} /></div>
          {c.diff.length > 0 && (
            <ul className="divide-y divide-border rounded-md border border-border">
              {c.diff.map((d) => <li key={d.field} className="flex flex-wrap justify-between gap-2 px-3 py-2"><span className="text-muted">{d.field}</span><span className="tnum break-all"><s className="text-danger">{show(d.before)}</s> → <span className="text-success">{show(d.after)}</span></span></li>)}
            </ul>
          )}
          {c.warnings.map((w) => <p key={w} role="status" className="rounded-md bg-warning/10 px-3 py-2 text-warning">{w}</p>)}
          {c.rollback_instructions && <details className="rounded-md bg-subtle px-3 py-2"><summary className="cursor-pointer font-medium">Rollback instructions</summary><p className="mt-1 text-muted">{c.rollback_instructions}</p></details>}
          {c.error && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-danger">{c.error}</p>}
          {c.status === "previewed" && (
            <div className="space-y-3 rounded-md border border-border p-3">
              <p className="font-medium">Confirm live DNS change</p>
              {!ownerOk && <p className="text-muted">Production DNS changes need owner-level approval (dns:manage). Ask a platform owner to confirm this change.</p>}
              <Field label={`Type ${domain} to confirm`}>{(p) => <Input {...p} value={typed} disabled={!ownerOk} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />}</Field>
              <label className="flex min-h-11 items-center gap-2"><input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" disabled={!ownerOk} checked={prod} onChange={(e) => setProd(e.target.checked)} /> I confirm this changes production DNS</label>
            </div>
          )}
          {c.status === "applied" && <p>Propagation: <StatusPill status={c.verification === "propagated" ? "verified" : c.verification} />{c.verification_detail && "observed" in c.verification_detail ? <span className="ml-2 text-muted">seen: {JSON.stringify(c.verification_detail.observed)}</span> : null}</p>}
          {c.status === "violation" && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-danger">Unrelated records differ after this change. Review the zone immediately.</p>}
          {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-danger">{err.message}</p>}
          <div className="flex flex-wrap justify-end gap-2">
            {c.status === "previewed" && <>
              <Button variant="outline" loading={discard.pending} onClick={async () => { if (await discard.run(undefined)) { onClose(); } }}>Discard</Button>
              <Button variant="danger" disabled={!ready} loading={confirm.pending} onClick={async () => { const r = await confirm.run(undefined); if (r) { setLocal(r); onReplace(r); toast({ title: r.status === "applied" ? "DNS change applied" : `Change ${r.status}`, tone: r.status === "applied" ? "success" : "danger" }); onApplied(); } }}>Apply change</Button>
            </>}
            {c.status === "applied" && <>
              <Button variant="outline" loading={verify.pending} onClick={async () => { const r = await verify.run(undefined); if (r) setLocal(r); }}><RefreshCw className="h-4 w-4" /> Verify propagation</Button>
              <Button variant="outline" loading={rollback.pending} onClick={async () => { const r = await rollback.run(undefined); if (r) { setLocal(r); onReplace(r); } }}><Undo2 className="h-4 w-4" /> Prepare rollback</Button>
            </>}
            <Button variant="outline" onClick={onClose}>Close</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ChangeHistory({ domain, q }: { domain: string; q: Record<string, string> }) {
  const { data, error, loading, reload } = useApi<{ items: Change[] }>(`/dns/${encodeURIComponent(domain)}/changes`, q);
  const [open, setOpen] = React.useState<Change | null>(null);
  return (
    <Card>
      <CardHeader title="Change history" description="Every DNS change request, newest first." />
      <CardBody>
        {loading ? <Skeleton className="h-20 w-full" /> : error ? <ErrorState onRetry={reload} /> : !data?.items.length ? <EmptyState title="No DNS changes yet" /> : (
          <ul className="divide-y divide-border text-sm">
            {data.items.map((c) => (
              <li key={c.id}><button className="flex min-h-11 w-full flex-wrap items-center justify-between gap-2 py-2 text-left" onClick={() => setOpen(c)}>
                <span>{c.action} · <span className="break-all">{(c.after ?? c.before)?.name}</span></span>
                <span className="flex items-center gap-2"><StatusPill status={c.status} /><span className="tnum text-muted">{fmtDate(c.created_at)}</span></span>
              </button></li>
            ))}
          </ul>
        )}
      </CardBody>
      <ChangeDialog change={open} domain={domain} q={q} onClose={() => setOpen(null)} onApplied={reload} onReplace={setOpen} />
    </Card>
  );
}

export function AuditHistory({ domain, q }: { domain?: string; q: Record<string, string> }) {
  const { data, error, loading, reload } = useApi<{ items: AuditItem[] }>("/dns/audit", { ...q, domain });
  return (
    <Card>
      <CardHeader title="Audit history" description="SSL, DNS and Cloudflare events." />
      <CardBody>
        {loading ? <Skeleton className="h-20 w-full" /> : error ? <ErrorState onRetry={reload} /> : !data?.items.length ? <EmptyState title="No audit events yet" /> : (
          <ul className="divide-y divide-border text-sm">
            {data.items.map((e) => <li key={e.id} className="flex flex-wrap justify-between gap-2 py-2"><span className="font-medium">{e.action}</span><span className="tnum text-muted">{fmtDate(e.at)}</span></li>)}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
