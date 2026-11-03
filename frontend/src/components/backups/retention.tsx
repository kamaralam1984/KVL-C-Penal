"use client";

import * as React from "react";
import { Pencil } from "lucide-react";
import { ResourceTable } from "@/components/resource/resource-table";
import { FormDialog } from "@/components/resource/form-dialog";
import { Can } from "@/components/resource/helpers";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button, IconButton } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { StatusPill, fmtBytes, fmtDate } from "@/components/resource/helpers";
import { useToast } from "@/components/ui/toast";
import { useApi } from "@/hooks/use-api";
import { ApiError, patch, post } from "@/lib/http";
import { TYPES, type Ctx, type Plan, type PlanRow, type Policy } from "./types";

const num = (name: string, label: string, d: number) => ({ name, label, type: "number" as const, required: true, defaultValue: d });

function Rows({ rows, tone }: { rows: PlanRow[]; tone: string }) {
  if (!rows.length) return <p className="text-sm text-muted">None.</p>;
  return (
    <ul className="divide-y divide-border text-sm">
      {rows.map((r) => (
        <li key={r.id} className="flex flex-wrap justify-between gap-x-3 py-2">
          <span className="tnum">{fmtDate(r.created_at)} · {fmtBytes(r.size_bytes)}</span>
          <span className={`text-xs ${tone}`}>{r.reasons.join(", ")}</span>
        </li>
      ))}
    </ul>
  );
}

export function RetentionTab({ ctx }: { ctx: Ctx }) {
  const toast = useToast();
  const [edit, setEdit] = React.useState<Policy | "new" | null>(null);
  const [n, setN] = React.useState(0);
  const pol = useApi<{ items: Policy[] }>("/backups/retention-policies", ctx.q);
  const [sim, setSim] = React.useState({ policy_id: "", backup_type: "website_files", resource_ref: "" });
  const [plan, setPlan] = React.useState<Plan>();
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string>();
  const [pruneOpen, setPruneOpen] = React.useState(false);
  const [typed, setTyped] = React.useState("");
  const cur = edit && edit !== "new" ? edit : null;
  const base = { policy_id: sim.policy_id, backup_type: sim.backup_type, resource_ref: sim.resource_ref || undefined };

  const run = async () => {
    setBusy(true); setErr(undefined); setPlan(undefined);
    try { setPlan(await post<Plan>("/backups/retention/simulate", ctx.body(base))); } catch (e) { setErr(e instanceof ApiError ? e.message : "Network error"); } finally { setBusy(false); }
  };
  const prune = async () => {
    try {
      await post("/backups/retention/prune", ctx.body({ ...base, confirm: true, confirmation: "PRUNE" }));
      toast({ title: "Prune job queued", tone: "success" }); setPruneOpen(false); setTyped(""); setPlan(undefined);
    } catch (e) { toast({ title: e instanceof ApiError ? e.message : "Network error", tone: "danger" }); }
  };
  return (
    <div className="space-y-4">
      <ResourceTable<Policy>
        key={n} path="/backups/retention-policies" query={ctx.q} caption="Retention policies"
        searchText={(p) => p.name} onCreate={() => setEdit("new")} createLabel="New policy" canCreate={!ctx.needTenant}
        columns={[
          { id: "name", header: "Name", cell: (p) => <span className="font-medium">{p.name}{p.tenant_id ? "" : " (platform default)"}</span> },
          { id: "last", header: "Last", cell: (p) => p.keep_last, align: "right" },
          { id: "d", header: "Daily", cell: (p) => p.keep_daily, align: "right", hideOnMobile: true },
          { id: "w", header: "Weekly", cell: (p) => p.keep_weekly, align: "right", hideOnMobile: true },
          { id: "m", header: "Monthly", cell: (p) => p.keep_monthly, align: "right", hideOnMobile: true },
          { id: "min", header: "Min kept", cell: (p) => p.min_keep, align: "right" },
        ]}
        rowActions={(p) => <Can perm="backups:write"><IconButton label="Edit policy" onClick={() => setEdit(p)}><Pencil className="h-4 w-4" /></IconButton></Can>}
      />
      <FormDialog
        open={!!edit} onOpenChange={(o) => !o && setEdit(null)} title={cur ? "Edit policy" : "New retention policy"} submitLabel="Save policy" successMessage="Policy saved"
        fields={[{ name: "name", label: "Name", required: true, defaultValue: cur?.name ?? "" }, num("keep_last", "Keep newest N", cur?.keep_last ?? 3), num("keep_daily", "Daily slots", cur?.keep_daily ?? 7), num("keep_weekly", "Weekly slots", cur?.keep_weekly ?? 4), num("keep_monthly", "Monthly slots", cur?.keep_monthly ?? 3), num("min_keep", "Always keep at least", cur?.min_keep ?? 2)]}
        onSubmit={async (v) => { if (cur) await patch(`/backups/retention-policies/${cur.id}`, v); else await post("/backups/retention-policies", ctx.body(v)); setN((x) => x + 1); pol.reload(); }}
      />
      <Card>
        <CardHeader title="Simulate retention" description="See what a policy would remove. A simulation never deletes anything." />
        <CardBody className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Policy">{(p) => <Select {...p} value={sim.policy_id} onChange={(e) => setSim({ ...sim, policy_id: e.target.value })}><option value="">Select…</option>{pol.data?.items.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select>}</Field>
            <Field label="Type">{(p) => <Select {...p} value={sim.backup_type} onChange={(e) => setSim({ ...sim, backup_type: e.target.value })}>{TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</Select>}</Field>
            <Field label="Resource">{(p) => <Input {...p} value={sim.resource_ref} onChange={(e) => setSim({ ...sim, resource_ref: e.target.value })} placeholder="website / database" />}</Field>
          </div>
          <Button className="min-h-11" loading={busy} disabled={!sim.policy_id || ctx.needTenant} onClick={run}>Run simulation</Button>
          {err && <p role="alert" className="text-sm text-danger">{err}</p>}
          {plan && (
            <div className="space-y-3">
              <p className="rounded-md bg-info/12 p-3 text-sm font-medium text-info" role="status">Simulation only: nothing was deleted ({plan.deleted} removed).</p>
              {plan.prune_blocked_reason && <p role="alert" className="rounded-md bg-warning/14 p-3 text-sm text-warning">{plan.prune_blocked_reason}</p>}
              <p className="text-sm text-muted">{plan.total_considered} considered · would free <strong className="text-text">{fmtBytes(plan.would_free_bytes)}</strong></p>
              <div className="grid gap-4 md:grid-cols-2">
                <div><h3 className="mb-1 flex items-center gap-2 text-sm font-semibold">Would keep <StatusPill status="ok" /></h3><Rows rows={plan.would_keep} tone="text-success" /></div>
                <div><h3 className="mb-1 text-sm font-semibold">Would prune ({plan.would_prune.length})</h3><Rows rows={plan.would_prune} tone="text-danger" /></div>
              </div>
              <Can perm="backups:manage">
                {plan.would_prune.length > 0 && !plan.prune_blocked_reason && <Button variant="danger" className="min-h-11" onClick={() => setPruneOpen(true)}>Prune these backups…</Button>}
              </Can>
            </div>
          )}
        </CardBody>
      </Card>
      <Dialog open={pruneOpen} onOpenChange={setPruneOpen}>
        <DialogContent title="Permanently delete old backups?" description="This deletes the listed backups from the server. A verified recovery point is always kept. This cannot be undone."
          footer={<><DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose><Button variant="danger" disabled={typed !== "PRUNE"} onClick={prune}>Prune backups</Button></>}>
          <Field label="Type PRUNE to confirm">{(p) => <Input {...p} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />}</Field>
        </DialogContent>
      </Dialog>
    </div>
  );
}
