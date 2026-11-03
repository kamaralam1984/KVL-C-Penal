"use client";

import * as React from "react";
import { Archive, KeyRound, Plus, RefreshCw, RotateCcw, Trash2, UserMinus } from "lucide-react";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog, Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { Can, DetailList, StatusPill, fmtBytes, fmtDate } from "@/components/resource/helpers";
import { CopyField } from "@/components/servers/enrollment-reveal";
import { Meter } from "@/components/applications/app-drawer";
import { useApi } from "@/hooks/use-api";
import { ApiError, post, del } from "@/lib/http";
import { ENGINE_LABEL, dbBusy, type BackupReq, type Credentials, type DbRow, type DbUser } from "./types";

type OnSecret = (c: Credentials, title: string) => void;
const ERR_TEXT: Record<string, string> = {
  backup_required: "A verified backup from the last 7 days is required first. Request a backup and wait for it to succeed with a verified checksum.",
  token_expired: "The confirmation token expired. Start the deletion again to get a new one.",
  token_invalid: "The confirmation token is not valid.",
  confirmation_required: "The typed name does not match.",
  backup_not_verified: "Only a completed, checksum-verified backup can be restored.",
  last_writer: "You cannot remove the last read/write user.",
};
const msg = (e: unknown) => (e instanceof ApiError ? (ERR_TEXT[e.code] ?? e.message) : "Network error");

function useAct(reload: () => void) {
  const toast = useToast();
  const [busy, setBusy] = React.useState(false);
  const run = async <T,>(fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
    setBusy(true);
    try {
      const r = await fn();
      if (ok) toast({ title: ok, tone: "success" });
      reload();
      return r;
    } catch (e) {
      toast({ title: "Action failed", description: msg(e), tone: "danger" });
    } finally {
      setBusy(false);
    }
  };
  return { busy, run };
}

export function DatabaseDrawer({ dbId, onClose, onSecret }: { dbId: string | null; onClose: () => void; onSecret: OnSecret }) {
  const [poll, setPoll] = React.useState(false);
  const q = useApi<DbRow>(dbId ? `/databases/${dbId}` : null, undefined, { refreshMs: poll ? 3000 : undefined });
  const d = q.data;
  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPoll(!!d && dbBusy(d));
  }, [d]);
  const closed = d?.status === "deleted";
  return (
    <Sheet open={!!dbId} onOpenChange={(o) => !o && onClose()}>
      <SheetContent title={d?.name ?? "Database"} description={d ? ENGINE_LABEL[d.engine] : "Loading…"} className="w-[min(96vw,44rem)]">
        <div className="p-4 sm:p-5">
          {q.loading && <Skeleton className="h-40 w-full" />}
          {q.error && (q.error.status === 404 ? <EmptyState title="Database deleted" description="This database no longer exists." /> : <ErrorState onRetry={q.reload} />)}
          {d && !closed && (
            <Tabs defaultValue="overview">
              <div className="-mx-1 overflow-x-auto">
                <TabsList className="min-w-max">
                  <TabsTrigger value="overview">Overview</TabsTrigger>
                  <TabsTrigger value="users">Users</TabsTrigger>
                  <TabsTrigger value="backups">Backups</TabsTrigger>
                  <TabsTrigger value="history">History</TabsTrigger>
                  <TabsTrigger value="danger">Danger zone</TabsTrigger>
                </TabsList>
              </div>
              <TabsContent value="overview" className="pt-4"><Overview d={d} reload={q.reload} onSecret={onSecret} /></TabsContent>
              <TabsContent value="users" className="pt-4"><Users d={d} reload={q.reload} onSecret={onSecret} /></TabsContent>
              <TabsContent value="backups" className="pt-4"><Backups d={d} reload={q.reload} /></TabsContent>
              <TabsContent value="history" className="pt-4"><History d={d} /></TabsContent>
              <TabsContent value="danger" className="pt-4"><Danger d={d} reload={q.reload} onDone={onClose} /></TabsContent>
            </Tabs>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Overview({ d, reload, onSecret }: { d: DbRow; reload: () => void; onSecret: OnSecret }) {
  const conn = useApi<{ host: string; port: number; database: string; remote_access: boolean; note: string; users: { username: string; privilege: string; uri_template: string }[] }>(d.status === "ready" ? `/databases/${d.id}/connection` : null);
  const usage = useApi<{ quotas: { resource: string; limit: number; used: number }[] }>(`/databases/${d.id}/usage`);
  const { busy, run } = useAct(() => {
    reload();
    setTimeout(reload, 3000);
  });
  const retryable = d.status === "failed" || d.status === "pending_server";
  return (
    <div className="space-y-4">
      {d.message && <p role="status" className={`rounded-md px-3 py-2 text-sm ${d.status === "failed" ? "bg-danger/10 text-danger" : "bg-subtle text-muted"}`}>{d.message}</p>}
      {d.status === "provisioning" && <p role="status" className="text-sm text-muted">Provisioning on the server. This page updates automatically.</p>}
      {retryable && (
        <Can perm="databases:write">
          <Button loading={busy} onClick={async () => {
            const r = await run(() => post<DbRow & { credentials: Credentials | null }>(`/databases/${d.id}/retry`), "Provisioning retried");
            if (r?.credentials) onSecret(r.credentials, "Database credentials");
          }}><RotateCcw className="h-4 w-4" /> Retry provisioning</Button>
        </Can>
      )}
      <Card>
        <CardBody className="pt-4">
          <DetailList items={[["Status", <StatusPill key="s" status={d.status} />], ["Engine", ENGINE_LABEL[d.engine]], ["Size", fmtBytes(d.size_bytes)], ["Size checked", fmtDate(d.size_checked_at)], ["Last verified backup", fmtDate(d.verified_backup_at)], ["Created", fmtDate(d.created_at)]]} />
          {d.status === "ready" && (
            <Button size="sm" variant="outline" className="mt-3" loading={busy} onClick={() => run(() => post(`/databases/${d.id}/size/refresh`), "Size refresh requested")}><RefreshCw className="h-3.5 w-3.5" /> Refresh size</Button>
          )}
        </CardBody>
      </Card>
      {conn.data && (
        <Card>
          <CardHeader title="Connection" description={conn.data.note} />
          <CardBody className="space-y-3">
            <CopyField label="Host : port" value={`${conn.data.host}:${conn.data.port}`} />
            <CopyField label="Database" value={conn.data.database} />
            {conn.data.users.map((u) => <CopyField key={u.username} label={`${u.username} (${u.privilege})`} value={u.uri_template} />)}
            <p className="text-xs text-muted">Passwords are never displayed here. Administrative credentials are never exposed.</p>
          </CardBody>
        </Card>
      )}
      <div className="space-y-3">{usage.data?.quotas.map((x) => <Meter key={x.resource} label={`Quota: ${x.resource.replace("_", " ")}`} used={x.used} limit={x.limit} />)}</div>
    </div>
  );
}

function Users({ d, reload, onSecret }: { d: DbRow; reload: () => void; onSecret: OnSecret }) {
  const { busy, run } = useAct(reload);
  const [add, setAdd] = React.useState(false);
  const [suffix, setSuffix] = React.useState("");
  const [priv, setPriv] = React.useState("readonly");
  const [rot, setRot] = React.useState<DbUser | null>(null);
  const [rm, setRm] = React.useState<DbUser | null>(null);
  const users = (d.users ?? []).filter((u) => u.status !== "dropped");
  const ready = d.status === "ready";
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">Every user is limited to this database only: no global privileges and no grant option.</p>
      <Can perm="databases:write">
        <Button size="sm" disabled={!ready} onClick={() => setAdd(true)}><Plus className="h-4 w-4" /> Add user</Button>
      </Can>
      {!users.length ? <EmptyState title="No users" /> : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {users.map((u) => (
            <li key={u.id} className="space-y-1 px-3 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0"><p className="text-sm font-medium break-all text-text">{u.username}</p><p className="text-xs text-muted">{u.grants}</p></div>
                <StatusPill status={u.status} />
              </div>
              <p className="text-xs text-muted">Password last set {fmtDate(u.password_rotated_at)}</p>
              <Can perm="databases:write">
                <div className="flex flex-wrap gap-2 pt-1">
                  <Button size="sm" variant="outline" disabled={!ready || !["active", "failed"].includes(u.status)} onClick={() => setRot(u)}><KeyRound className="h-3.5 w-3.5" /> Rotate credentials</Button>
                  <Button size="sm" variant="ghost" disabled={!ready || u.status !== "active"} onClick={() => setRm(u)}><UserMinus className="h-3.5 w-3.5" /> Remove</Button>
                </div>
              </Can>
            </li>
          ))}
        </ul>
      )}
      <Dialog open={add} onOpenChange={setAdd}>
        <DialogContent title="Add database user" description="A password is generated and shown once."
          footer={<><DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose>
            <Button loading={busy} disabled={!/^[a-z][a-z0-9]{0,5}$/.test(suffix)} onClick={async () => {
              const r = await run(() => post<DbUser & { credentials: Credentials }>(`/databases/${d.id}/users`, { suffix, privilege: priv }), "User requested");
              if (r) { setAdd(false); setSuffix(""); onSecret(r.credentials, "New user credentials"); }
            }}>Create user</Button></>}>
          <div className="space-y-4">
            <Field label="Name suffix" hint={`Username: ${d.name}_${suffix || "…"} (1-6 lowercase letters/digits)`}>
              {(p) => <Input {...p} value={suffix} maxLength={6} onChange={(e) => setSuffix(e.target.value.toLowerCase())} />}
            </Field>
            <Field label="Permissions">
              {(p) => <Select {...p} value={priv} onChange={(e) => setPriv(e.target.value)}><option value="readonly">Read-only (SELECT)</option><option value="readwrite">Read and write</option></Select>}
            </Field>
          </div>
        </DialogContent>
      </Dialog>
      <ConfirmDialog open={!!rot} onOpenChange={(o) => !o && setRot(null)} title={`Rotate credentials for ${rot?.username}?`} description="A new password is generated and shown once. Applications using the old password stop working until updated." confirmLabel="Rotate" destructive
        onConfirm={async () => { if (!rot) return; const r = await run(() => post<{ credentials: Credentials }>(`/databases/${d.id}/users/${rot.id}/rotate`), "Rotation requested"); if (r) onSecret(r.credentials, "Rotated credentials"); }} />
      <ConfirmDialog open={!!rm} onOpenChange={(o) => !o && setRm(null)} title={`Remove ${rm?.username}?`} description="The user is dropped from the database server." confirmLabel="Remove" destructive onConfirm={() => rm && run(() => del(`/databases/${d.id}/users/${rm.id}`), "Removal requested")} />
    </div>
  );
}

function Backups({ d, reload }: { d: DbRow; reload: () => void }) {
  const [poll, setPoll] = React.useState(false);
  const reqs = useApi<BackupReq[]>(`/databases/${d.id}/requests`, undefined, { refreshMs: poll ? 3000 : undefined });
  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPoll(!!reqs.data?.some((r) => ["running", "approved"].includes(r.state)));
  }, [reqs.data]);
  const { busy, run } = useAct(() => { reqs.reload(); reload(); });
  const [approve, setApprove] = React.useState<BackupReq | null>(null);
  const [typed, setTyped] = React.useState("");
  const ready = d.status === "ready";
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">Restores overwrite current data. They need a completed, checksum-verified backup and your explicit approval with the database name typed.</p>
      <Can perm="databases:write"><Button loading={busy} disabled={!ready} onClick={() => run(() => post(`/databases/${d.id}/backups`), "Backup requested")}><Archive className="h-4 w-4" /> Request backup</Button></Can>
      {reqs.loading ? <Skeleton className="h-20 w-full" /> : reqs.error ? <ErrorState onRetry={reqs.reload} /> : !reqs.data?.length ? <EmptyState title="No backups or restores yet" /> : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {reqs.data.map((r) => (
            <li key={r.id} className="space-y-1.5 px-3 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium text-text capitalize">{r.kind}</p>
                <StatusPill status={r.state} />
              </div>
              <p className="text-xs break-all text-muted">{r.artifact ?? "—"} · {fmtDate(r.created_at)}</p>
              {r.kind === "backup" && r.state === "succeeded" && <p className="text-xs text-muted">{fmtBytes(r.size_bytes)} · {r.verified ? <span className="text-success">checksum verified</span> : <span className="text-warning">not verified, cannot be used to restore</span>}</p>}
              {r.error && <p className="rounded-md bg-danger/10 px-2 py-1.5 text-xs break-words text-danger">{r.error}</p>}
              <Can perm="backups:restore">
                <div className="flex flex-wrap gap-2">
                  {r.kind === "backup" && r.state === "succeeded" && r.verified && ready && (
                    <Button size="sm" variant="outline" onClick={() => run(() => post(`/databases/${d.id}/restores`, { source_backup_id: r.id }), "Restore requested, awaiting approval")}>Restore from this backup</Button>
                  )}
                  {r.state === "requested" && (
                    <>
                      <Button size="sm" onClick={() => { setTyped(""); setApprove(r); }}>Approve</Button>
                      <Button size="sm" variant="ghost" onClick={() => run(() => post(`/databases/${d.id}/requests/${r.id}/reject`), "Request rejected")}>Reject</Button>
                    </>
                  )}
                </div>
              </Can>
            </li>
          ))}
        </ul>
      )}
      <Dialog open={!!approve} onOpenChange={(o) => !o && setApprove(null)}>
        <DialogContent title={approve?.kind === "restore" ? "Approve restore" : "Approve request"} description={approve?.kind === "restore" ? "This overwrites the current database contents with the backup." : "Run this request now."}
          footer={<><DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose>
            <Button variant="danger" loading={busy} disabled={approve?.kind === "restore" && typed !== d.name} onClick={async () => { if (!approve) return; const r = await run(() => post(`/databases/${d.id}/requests/${approve.id}/approve`, { confirm_name: typed || undefined }), "Approved"); if (r) setApprove(null); }}>Approve</Button></>}>
          {approve?.kind === "restore" && (
            <Field label={`Type ${d.name} to confirm`}>{(p) => <Input {...p} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />}</Field>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function History({ d }: { d: DbRow }) {
  const h = useApi<{ action: string; summary: string; created_at: string }[]>(`/databases/${d.id}/history`);
  if (h.loading) return <Skeleton className="h-24 w-full" />;
  if (h.error) return <ErrorState onRetry={h.reload} />;
  if (!h.data?.length) return <EmptyState title="No history yet" />;
  return (
    <ol className="relative space-y-3 border-l border-border pl-4">
      {h.data.map((e, i) => (
        <li key={i} className="relative"><span className="absolute top-1.5 -left-[21px] h-2.5 w-2.5 rounded-full bg-primary" aria-hidden /><p className="text-sm text-text">{e.summary}</p><p className="text-xs text-muted">{fmtDate(e.created_at)}</p></li>
      ))}
    </ol>
  );
}

function Danger({ d, reload, onDone }: { d: DbRow; reload: () => void; onDone: () => void }) {
  const toast = useToast();
  const [tok, setTok] = React.useState<{ token: string; has_verified_backup: boolean } | null>(null);
  const [typed, setTyped] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string>();
  async function start() {
    setBusy(true);
    setErr(undefined);
    try { setTok(await post(`/databases/${d.id}/destroy-token`)); } catch (e) { setErr(msg(e)); } finally { setBusy(false); }
  }
  async function destroy() {
    if (!tok) return;
    setBusy(true);
    setErr(undefined);
    try {
      await post(`/databases/${d.id}/destroy`, { confirm_name: typed, token: tok.token });
      toast({ title: "Deletion started", tone: "success" });
      setTok(null);
      reload();
      onDone();
    } catch (e) { setErr(msg(e)); } finally { setBusy(false); }
  }
  return (
    <Can perm="databases:write" fallback={<p className="text-sm text-muted">Your role cannot delete databases.</p>}>
      <Card className="border-danger/40">
        <CardHeader title="Delete database" description="Permanently removes the database and its users. Requires a verified backup from the last 7 days, a one-time token and the typed database name." />
        <CardBody className="space-y-3">
          {!tok ? (
            <Button variant="danger" loading={busy} disabled={d.status === "deleting"} onClick={start}><Trash2 className="h-4 w-4" /> Start deletion</Button>
          ) : (
            <>
              {!tok.has_verified_backup && d.status === "ready" && <p role="alert" className="rounded-md bg-warning/14 px-3 py-2 text-sm text-warning">{ERR_TEXT.backup_required}</p>}
              <CopyField label="Confirmation token (expires in 10 minutes, single use)" value={tok.token} />
              <Field label={`Type ${d.name} to confirm`}>{(p) => <Input {...p} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />}</Field>
              <div className="flex gap-2">
                <Button variant="danger" loading={busy} disabled={typed !== d.name} onClick={destroy}>Delete permanently</Button>
                <Button variant="outline" onClick={() => { setTok(null); setTyped(""); setErr(undefined); }}>Cancel</Button>
              </div>
            </>
          )}
          {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err}</p>}
        </CardBody>
      </Card>
    </Can>
  );
}
