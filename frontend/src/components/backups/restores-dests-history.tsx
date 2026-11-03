"use client";

import * as React from "react";
import { Trash2 } from "lucide-react";
import { ResourceTable } from "@/components/resource/resource-table";
import { FormDialog } from "@/components/resource/form-dialog";
import { Can, RequirePerm, StatusPill, fmtDate } from "@/components/resource/helpers";
import { Button, IconButton } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { del, post } from "@/lib/http";
import type { Ctx, Dest, Restore } from "./types";

export function RestoresTab({ ctx }: { ctx: Ctx }) {
  const toast = useToast();
  const [n, setN] = React.useState(0);
  const approve = async (r: Restore) => {
    try { await post(`/backups/restores/${r.id}/approve`); toast({ title: "Restore approved and started", tone: "success" }); setN((x) => x + 1); } catch (e) { toast({ title: (e as Error).message, tone: "danger" }); }
  };
  return (
    <ResourceTable<Restore>
      key={n} path="/backups/restores" query={ctx.q} caption="Restores" refreshMs={10000}
      searchText={(r) => `${r.mode} ${r.status}`}
      emptyTitle="No restores yet" emptyDescription="Start one from a backup's detail panel."
      columns={[
        { id: "mode", header: "Mode", cell: (r) => (r.mode === "overwrite" ? "Overwrite live" : "Isolated") },
        { id: "st", header: "Status", cell: (r) => <StatusPill status={r.status} /> },
        { id: "loc", header: "Location", cell: (r) => r.location ?? "—", hideOnMobile: true },
        { id: "at", header: "Requested", cell: (r) => fmtDate(r.created_at), sortValue: (r) => r.created_at },
      ]}
      rowActions={(r) =>
        r.status === "pending_approval" ? (
          <Can perm="backups:manage"><Button size="sm" title="A different person than the requester must approve" onClick={() => approve(r)}>Approve</Button></Can>
        ) : r.error ? <span className="text-xs text-danger">{r.error.slice(0, 40)}</span> : null
      }
    />
  );
}

const sftp = (v: Record<string, string | number | boolean>) => v.kind === "sftp";
const s3 = (v: Record<string, string | number | boolean>) => v.kind === "s3";

export function DestinationsTab({ ctx }: { ctx: Ctx }) {
  const [open, setOpen] = React.useState(false);
  const [rm, setRm] = React.useState<Dest | null>(null);
  const [n, setN] = React.useState(0);
  const toast = useToast();
  const test = async (d: Dest) => {
    try { await post(`/backups/destinations/${d.id}/test`); setN((x) => x + 1); toast({ title: "Test requested", tone: "success" }); } catch (e) { toast({ title: (e as Error).message, tone: "danger" }); }
  };
  return (
    <RequirePerm perm="backups:manage">
      <p className="mb-3 text-sm text-muted">Off-site destinations stay <strong>not configured</strong> until you add one. Local-path copies work; SFTP and S3 credentials are stored encrypted but transfer is not implemented yet.</p>
      <ResourceTable<Dest>
        key={n} path="/backups/destinations" query={ctx.q} caption="Destinations" searchText={(d) => `${d.name} ${d.kind}`}
        onCreate={() => setOpen(true)} createLabel="Add destination"
        emptyTitle="Not configured" emptyDescription="No off-site destination is set up."
        columns={[
          { id: "name", header: "Name", cell: (d) => <span className="font-medium">{d.name}</span> },
          { id: "kind", header: "Kind", cell: (d) => d.kind.toUpperCase() },
          { id: "st", header: "Status", cell: (d) => <StatusPill status={d.status} /> },
          { id: "cred", header: "Credentials", cell: (d) => (d.has_credentials ? "stored (hidden)" : "none"), hideOnMobile: true },
          { id: "note", header: "Note", cell: (d) => d.last_error ?? d.note ?? "", hideOnMobile: true },
        ]}
        rowActions={(d) => <div className="flex gap-1"><Button size="sm" variant="outline" onClick={() => test(d)}>Test</Button><IconButton label="Delete destination" onClick={() => setRm(d)}><Trash2 className="h-4 w-4" /></IconButton></div>}
      />
      <FormDialog
        open={open} onOpenChange={setOpen} title="Add off-site destination" submitLabel="Save destination" successMessage="Destination saved"
        fields={[
          { name: "name", label: "Name", required: true },
          { name: "kind", label: "Kind", type: "select", required: true, defaultValue: "local", options: [{ value: "local", label: "Local / mounted path" }, { value: "sftp", label: "SFTP" }, { value: "s3", label: "S3-compatible" }] },
          { name: "path", label: "Path", required: true, showIf: (v) => v.kind !== "s3", hint: "Local: relative path on the server. SFTP: remote path." },
          { name: "host", label: "Host", showIf: sftp }, { name: "username", label: "Username", showIf: sftp },
          { name: "password", label: "Password (write-only)", type: "password", showIf: sftp },
          { name: "bucket", label: "Bucket", showIf: s3 }, { name: "region", label: "Region", showIf: s3 }, { name: "endpoint", label: "Endpoint (optional)", showIf: s3 },
          { name: "access_key_id", label: "Access key ID", type: "password", showIf: s3 },
          { name: "secret_access_key", label: "Secret access key (write-only)", type: "password", showIf: s3 },
        ]}
        onSubmit={async (v) => {
          const { name, kind, password, access_key_id, secret_access_key, ...cfg } = v as Record<string, string>;
          const credentials = kind === "sftp" ? { password } : kind === "s3" ? { access_key_id, secret_access_key } : undefined;
          await post("/backups/destinations", ctx.body({ name, kind, config: cfg, credentials }));
          setN((x) => x + 1);
        }}
      />
      <ConfirmDialog open={!!rm} onOpenChange={(o) => !o && setRm(null)} destructive title="Remove destination?" description="Only the destination setting is removed; existing backups are not deleted." confirmLabel="Remove"
        onConfirm={async () => { if (rm) { await del(`/backups/destinations/${rm.id}`); setN((x) => x + 1); } }} />
    </RequirePerm>
  );
}

interface Job { id: string; operation: string; status: string; error: string | null; created_at: string; finished_at: string | null }
export function HistoryTab({ ctx }: { ctx: Ctx }) {
  return (
    <ResourceTable<Job>
      path="/backups/history" query={ctx.q} caption="Job history" refreshMs={15000} searchText={(j) => `${j.operation} ${j.status}`}
      emptyTitle="No backup jobs yet"
      columns={[
        { id: "op", header: "Operation", cell: (j) => j.operation.replace("backups.", "") },
        { id: "st", header: "Status", cell: (j) => <StatusPill status={j.status} /> },
        { id: "err", header: "Error", cell: (j) => j.error ?? "", hideOnMobile: true },
        { id: "at", header: "Queued", cell: (j) => fmtDate(j.created_at), sortValue: (j) => j.created_at },
        { id: "fin", header: "Finished", cell: (j) => fmtDate(j.finished_at), hideOnMobile: true },
      ]}
    />
  );
}
