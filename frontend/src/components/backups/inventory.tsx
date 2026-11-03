"use client";

import * as React from "react";
import { Archive, ShieldCheck } from "lucide-react";
import { ResourceTable } from "@/components/resource/resource-table";
import { FormDialog } from "@/components/resource/form-dialog";
import { Can, DetailList, StatusPill, fmtBytes, fmtDate } from "@/components/resource/helpers";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { useToast } from "@/components/ui/toast";
import { post } from "@/lib/http";
import { TYPES, typeLabel, type Ctx, type Rec } from "./types";
import { RestoreWizard } from "./restore-wizard";

export function Inventory({ ctx }: { ctx: Ctx }) {
  const toast = useToast();
  const [create, setCreate] = React.useState(false);
  const [sel, setSel] = React.useState<Rec | null>(null);
  const [wiz, setWiz] = React.useState<Rec | null>(null);
  const [nonce, setNonce] = React.useState(0);
  const reload = () => setNonce((n) => n + 1);
  const verify = async (r: Rec) => {
    try {
      await post(`/backups/${r.id}/verify`, { deep: false });
      toast({ title: "Verification started", tone: "success" });
      setWiz(null); setSel(null); reload();
    } catch (e) { toast({ title: (e as Error).message, tone: "danger" }); }
  };
  return (
    <>
      <ResourceTable<Rec & { id: string }>
        key={nonce}
        path="/backups" query={ctx.q} caption="Backups" refreshMs={15000}
        searchText={(r) => `${r.backup_type} ${r.resource_ref ?? ""} ${r.status}`}
        onCreate={() => setCreate(true)} createLabel="Back up now" canCreate={!ctx.needTenant}
        emptyTitle="No backups yet" emptyDescription="Request a backup or create a schedule."
        columns={[
          { id: "type", header: "Type", cell: (r) => typeLabel(r.backup_type), sortValue: (r) => r.backup_type },
          { id: "res", header: "Resource", cell: (r) => r.resource_ref ?? "—", hideOnMobile: true },
          { id: "st", header: "Status", cell: (r) => <StatusPill status={r.status} /> },
          { id: "v", header: "Verification", cell: (r) => <StatusPill status={r.verification_status} />, hideOnMobile: true },
          { id: "size", header: "Size", cell: (r) => <span className="tnum">{fmtBytes(r.size_bytes)}</span>, sortValue: (r) => r.size_bytes ?? 0, align: "right", hideOnMobile: true },
          { id: "sha", header: "SHA-256", cell: (r) => <code className="font-mono text-xs">{r.sha256?.slice(0, 10) ?? "—"}</code>, hideOnMobile: true },
          { id: "at", header: "Created", cell: (r) => fmtDate(r.created_at), sortValue: (r) => r.created_at },
        ]}
        rowActions={(r) => <Button size="sm" variant="outline" onClick={() => setSel(r)}>Details</Button>}
      />
      <FormDialog
        open={create} onOpenChange={setCreate} title="Back up now" description="Starts a backup job on the server."
        submitLabel="Start backup" successMessage="Backup requested"
        fields={[
          { name: "backup_type", label: "Type", type: "select", required: true, options: TYPES, defaultValue: "website_files" },
          { name: "resource_ref", label: "Website / database reference", hint: "Not needed for SaaS configuration metadata.", showIf: (v) => v.backup_type !== "saas_config" },
          { name: "encrypt", label: "Encrypt this backup (needs a key on the server)", type: "checkbox" },
        ]}
        onSubmit={async (v) => { await post("/backups", ctx.body(v)); reload(); }}
      />
      <Sheet open={!!sel} onOpenChange={(o) => !o && setSel(null)}>
        {sel && (
          <SheetContent title="Backup detail" description={`${typeLabel(sel.backup_type)}${sel.resource_ref ? ` · ${sel.resource_ref}` : ""}`}>
            <div className="space-y-4 p-5">
              {sel.confirmed_recovery_point && <Badge tone="success"><ShieldCheck className="mr-1 h-3.5 w-3.5" />Confirmed recovery point</Badge>}
              {sel.verification_status === "mismatch" && <p role="alert" className="rounded-md bg-danger/10 p-3 text-sm text-danger">Checksum mismatch: this backup no longer matches its recorded checksum. Do not rely on it.</p>}
              {(sel.status === "failed" || sel.verification_status === "failed") && <p role="alert" className="rounded-md bg-danger/10 p-3 text-sm text-danger">{sel.error ?? "This backup failed."}</p>}
              <DetailList items={[["Status", <StatusPill key="s" status={sel.status} />], ["Verification", <StatusPill key="v" status={sel.verification_status} />], ["Verified at", fmtDate(sel.verified_at)], ["Size", fmtBytes(sel.size_bytes)], ["Files", sel.entries], ["SHA-256", <code key="h" className="font-mono text-xs break-all">{sel.sha256 ?? "—"}</code>], ["Encrypted", sel.encrypted ? `Yes (key ${sel.key_id ?? "?"})` : "No"], ["Trigger", sel.trigger], ["Off-site", <StatusPill key="o" status={sel.offsite_status} />], ["Started", fmtDate(sel.started_at)], ["Finished", fmtDate(sel.finished_at)]]} />
              <Can perm="backups:write">
                {sel.status === "completed" && (
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" className="min-h-11" onClick={() => verify(sel)}>Verify now</Button>
                    <Can perm="backups:restore"><Button className="min-h-11" onClick={() => { setWiz(sel); setSel(null); }}><Archive className="h-4 w-4" />Restore…</Button></Can>
                  </div>
                )}
              </Can>
            </div>
          </SheetContent>
        )}
      </Sheet>
      <RestoreWizard rec={wiz} ctx={ctx} onClose={() => setWiz(null)} onVerify={verify} />
    </>
  );
}
