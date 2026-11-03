"use client";

import * as React from "react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KpiCard } from "@/components/ui/kpi-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/states";
import { ResourceTable } from "@/components/resource/resource-table";
import { FormDialog } from "@/components/resource/form-dialog";
import { LiveBadge, StatusPill, fmtDate } from "@/components/resource/helpers";
import { useAuth } from "@/components/providers/auth-provider";
import { useApi } from "@/hooks/use-api";
import { post } from "@/lib/http";
import { Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";

interface Aging { open_total: number; buckets: Record<string, number>; by_priority: Record<string, number>; oldest: { id: string; number: number; subject: string; priority: string; age_hours: number }[] }
interface SlaRep { days: number; tickets: number; clock: string; first_response: { met: number; breached: number; pending: number; compliance_pct: number | null }; resolution: { met: number; breached: number; pending: number; compliance_pct: number | null } }
interface Fr { overall: { count: number; avg_minutes: number | null; median_minutes: number | null }; by_priority: Record<string, { count: number; avg_minutes: number | null }> }
interface Work { staff: { user_id: string | null; name: string; open: number; breached: number; resolved_in_period: number }[] }
const pct = (v: number | null) => (v === null ? "n/a" : `${v}%`);
const mins = (v: number | null) => (v === null ? "n/a" : v >= 120 ? `${(v / 60).toFixed(1)} h` : `${v.toFixed(0)} min`);

export function ReportsPanel() {
  const [days, setDays] = React.useState("30");
  const toast = useToast();
  const aging = useApi<Aging>("/support/reports/aging");
  const sla = useApi<SlaRep>("/support/reports/sla", { days });
  const fr = useApi<Fr>("/support/reports/first-response", { days });
  const work = useApi<Work>("/support/reports/workload", { days });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="Report period" className="w-40" value={days} onChange={(e) => setDays(e.target.value)}>
          {["7", "30", "90"].map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </Select>
        <LiveBadge />
        <Button size="sm" variant="outline" onClick={async () => { const r = await post<Record<string, number>>("/support/sla/run"); toast({ title: `SLA check: ${r.escalated} escalated`, tone: "info" }); aging.reload(); sla.reload(); }}>Run SLA check now</Button>
      </div>
      {(aging.error || sla.error) && <ErrorState onRetry={() => { aging.reload(); sla.reload(); }} />}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard label="Open tickets" value={String(aging.data?.open_total ?? "—")} hint="Open, pending or on hold" />
        <KpiCard label="First-response SLA" value={pct(sla.data?.first_response.compliance_pct ?? null)} hint={`${sla.data?.first_response.breached ?? 0} breached`} />
        <KpiCard label="Resolution SLA" value={pct(sla.data?.resolution.compliance_pct ?? null)} hint={`${sla.data?.resolution.breached ?? 0} breached`} />
        <KpiCard label="Median first response" value={mins(fr.data?.overall.median_minutes ?? null)} hint={`${fr.data?.overall.count ?? 0} answered`} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Ticket aging" description="Open tickets by age" />
          <CardBody>
            <dl className="grid grid-cols-2 gap-2 text-sm">
              {aging.data && Object.entries(aging.data.buckets).map(([k, v]) => (<div key={k} className="flex justify-between rounded-md bg-subtle px-3 py-2"><dt className="text-muted">{({ lt_1d: "< 1 day", d1_3: "1–3 days", d3_7: "3–7 days", gt_7d: "> 7 days" } as Record<string, string>)[k]}</dt><dd className="tnum font-medium text-text">{v}</dd></div>))}
            </dl>
            <p className="mt-3 text-xs text-muted">SLA clock: {sla.data?.clock === "24x7" ? "24x7 (calendar time)" : "business hours (UTC)"}. Tickets on hold pause the clock.</p>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Staff workload" description="Open assigned tickets and recent resolutions" />
          <CardBody className="space-y-2">
            {work.data?.staff.length === 0 && <p className="text-sm text-muted">No tickets yet.</p>}
            {work.data?.staff.map((w) => (
              <div key={w.user_id ?? "none"} className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm">
                <span className="font-medium text-text">{w.name}</span>
                <span className="flex items-center gap-2"><span className="tnum text-muted">{w.open} open</span>{w.breached > 0 && <Badge tone="danger">{w.breached} breached</Badge>}<span className="tnum text-muted">{w.resolved_in_period} resolved</span></span>
              </div>
            ))}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

interface Member { id: string; name: string; email: string; status: string; role_label: string; departments: string[]; last_login_at: string | null }
interface Dept { id: string; name: string; description: string | null; active: boolean; members: number }

/** Staff accounts + departments. Staff list needs staff:read (platform owner); departments need tickets:manage. */
export function StaffPanel() {
  const { can } = useAuth();
  const [dept, setDept] = React.useState(false);
  const [detail, setDetail] = React.useState<string | null>(null);
  const info = useApi<{ roles: { label: string; permissions: string[]; infrastructure_permissions: string[] }[]; workload: { open: number; breached: number; resolved_in_period: number } }>(detail ? `/staff/members/${detail}` : null);
  return (
    <div className="space-y-4">
      {can("staff:read") ? (
        <ResourceTable<Member> path="/staff/members" caption="Staff accounts" searchText={(m) => `${m.name} ${m.email} ${m.role_label}`}
          columns={[
            { id: "name", header: "Name", cell: (m) => <button className="text-left font-medium text-primary underline-offset-2 hover:underline" onClick={() => setDetail(m.id)}>{m.name}</button>, sortValue: (m) => m.name },
            { id: "role", header: "Role", cell: (m) => m.role_label },
            { id: "dept", header: "Departments", cell: (m) => m.departments.join(", ") || "—", hideOnMobile: true },
            { id: "status", header: "Status", cell: (m) => <StatusPill status={m.status} /> },
            { id: "login", header: "Last sign-in", cell: (m) => fmtDate(m.last_login_at), hideOnMobile: true },
          ]} />
      ) : (
        <Card><CardBody className="pt-4 text-sm text-muted">Staff account administration is limited to the platform owner. Your department and workload are shown in Reports.</CardBody></Card>
      )}
      {detail && info.data && (
        <Card>
          <CardHeader title="Scoped permissions" description={`${info.data.workload.open} open · ${info.data.workload.resolved_in_period} resolved (30 d)`} actions={<Button size="sm" variant="outline" onClick={() => setDetail(null)}>Close</Button>} />
          <CardBody className="space-y-2 text-sm">
            {info.data.roles.map((r) => (
              <div key={r.label}><p className="font-medium text-text">{r.label}</p>
                <p className="text-muted">{r.infrastructure_permissions.length === 0 ? "No infrastructure permissions." : `Infrastructure: ${r.infrastructure_permissions.join(", ")}`}</p>
                <p className="mt-1 flex flex-wrap gap-1">{r.permissions.map((p) => <Badge key={p}>{p}</Badge>)}</p></div>
            ))}
          </CardBody>
        </Card>
      )}
      <ResourceTable<Dept> path="/support/departments" caption="Departments" searchText={(d) => d.name} onCreate={() => setDept(true)} createLabel="New department"
        columns={[
          { id: "name", header: "Department", cell: (d) => <span className="font-medium text-text">{d.name}</span>, sortValue: (d) => d.name },
          { id: "desc", header: "Description", cell: (d) => d.description ?? "—", hideOnMobile: true },
          { id: "members", header: "Members", cell: (d) => d.members, align: "right" },
          { id: "active", header: "Status", cell: (d) => <StatusPill status={d.active ? "active" : "disabled"} /> },
        ]} />
      <FormDialog open={dept} onOpenChange={setDept} title="New department" fields={[{ name: "name", label: "Name", required: true }, { name: "description", label: "Description" }]}
        onSubmit={(v) => post("/support/departments", v)} />
    </div>
  );
}
