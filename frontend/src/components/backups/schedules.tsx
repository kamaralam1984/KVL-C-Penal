"use client";

import * as React from "react";
import { Pencil, Trash2 } from "lucide-react";
import { ResourceTable } from "@/components/resource/resource-table";
import { FormDialog } from "@/components/resource/form-dialog";
import { StatusPill, fmtDate, Can } from "@/components/resource/helpers";
import { ConfirmDialog } from "@/components/ui/dialog";
import { IconButton } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { del, patch, post } from "@/lib/http";
import { TYPES, typeLabel, type Ctx, type Schedule } from "./types";

export function describeCron(c: string): string {
  const p = c.trim().split(/\s+/);
  if (p.length !== 5) return "Five fields: minute hour day-of-month month weekday";
  const [m, h, dom, mon, dow] = p;
  const time = /^\d+$/.test(m) && /^\d+$/.test(h) ? `${h.padStart(2, "0")}:${m.padStart(2, "0")}` : null;
  if (time && dom === "*" && mon === "*" && dow === "*") return `Every day at ${time}`;
  if (time && dom === "*" && mon === "*" && /^\d$/.test(dow)) return `Every ${["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][Number(dow)]} at ${time}`;
  if (time && /^\d+$/.test(dom) && mon === "*" && dow === "*") return `Monthly on day ${dom} at ${time}`;
  if (/^\d+$/.test(m) && h === "*") return `Hourly at minute ${m}`;
  return "Custom schedule";
}

export function SchedulesTab({ ctx }: { ctx: Ctx }) {
  const [edit, setEdit] = React.useState<Schedule | "new" | null>(null);
  const [rm, setRm] = React.useState<Schedule | null>(null);
  const [n, setN] = React.useState(0);
  const bump = () => setN((x) => x + 1);
  const cur = edit && edit !== "new" ? edit : null;
  const fields = React.useMemo(() => [
    { name: "name", label: "Name", required: true, defaultValue: cur?.name ?? "" },
    ...(cur ? [] : [
      { name: "backup_type", label: "Type", type: "select" as const, required: true, options: TYPES, defaultValue: "website_files" },
      { name: "resource_ref", label: "Website / database reference", showIf: (v: Record<string, string | number | boolean>) => v.backup_type !== "saas_config" },
    ]),
    { name: "cron", label: "Schedule (cron)", required: true, placeholder: "30 2 * * *", hint: "minute hour day month weekday, e.g. 30 2 * * * = daily 02:30 (at most 4 runs per hour).", defaultValue: cur?.cron ?? "30 2 * * *" },
    { name: "enabled", label: "Enabled", type: "checkbox" as const, defaultValue: cur?.enabled ?? true },
    { name: "encrypt", label: "Encrypt backups", type: "checkbox" as const, defaultValue: cur?.encrypt ?? false },
  ], [cur]);
  return (
    <>
      <ResourceTable<Schedule>
        key={n} path="/backups/schedules" query={ctx.q} caption="Schedules"
        searchText={(s) => `${s.name} ${s.backup_type} ${s.resource_ref ?? ""}`}
        onCreate={() => setEdit("new")} createLabel="New schedule" canCreate={!ctx.needTenant}
        emptyTitle="No schedules" emptyDescription="Create a schedule to back up automatically."
        columns={[
          { id: "name", header: "Name", cell: (s) => <span className="font-medium">{s.name}</span>, sortValue: (s) => s.name },
          { id: "type", header: "Type", cell: (s) => `${typeLabel(s.backup_type)}${s.resource_ref ? ` · ${s.resource_ref}` : ""}`, hideOnMobile: true },
          { id: "cron", header: "When", cell: (s) => <span title={s.cron}>{describeCron(s.cron)}</span> },
          { id: "en", header: "State", cell: (s) => (s.enabled ? <StatusPill status="active" /> : <StatusPill status="disabled" />) },
          { id: "next", header: "Next run", cell: (s) => fmtDate(s.next_run_at), hideOnMobile: true },
          { id: "enc", header: "", cell: (s) => (s.encrypt ? <Badge>encrypted</Badge> : null), hideOnMobile: true },
        ]}
        rowActions={(s) => (
          <Can perm="backups:write">
            <div className="flex gap-1">
              <IconButton label="Edit schedule" onClick={() => setEdit(s)}><Pencil className="h-4 w-4" /></IconButton>
              <IconButton label="Delete schedule" onClick={() => setRm(s)}><Trash2 className="h-4 w-4" /></IconButton>
            </div>
          </Can>
        )}
      />
      <FormDialog
        open={!!edit} onOpenChange={(o) => !o && setEdit(null)} title={cur ? "Edit schedule" : "New schedule"} fields={fields}
        submitLabel="Save schedule" successMessage="Schedule saved"
        onSubmit={async (v) => { if (cur) await patch(`/backups/schedules/${cur.id}`, v); else await post("/backups/schedules", ctx.body(v)); bump(); }}
      />
      <ConfirmDialog
        open={!!rm} onOpenChange={(o) => !o && setRm(null)} destructive title="Delete this schedule?"
        description="Only the schedule is removed. Existing backups are kept and are not deleted."
        confirmLabel="Delete schedule" onConfirm={async () => { if (rm) { await del(`/backups/schedules/${rm.id}`); bump(); } }}
      />
    </>
  );
}
