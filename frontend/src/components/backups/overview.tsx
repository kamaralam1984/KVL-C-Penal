"use client";

import { Archive, CheckCircle2, Clock, HardDrive, ShieldAlert, XCircle } from "lucide-react";
import { KpiCard } from "@/components/ui/kpi-card";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { fmtBytes, fmtDate, StatusPill } from "@/components/resource/helpers";
import { useApi } from "@/hooks/use-api";
import { typeLabel, type Ctx, type Overview } from "./types";

export function OverviewTab({ ctx }: { ctx: Ctx }) {
  const { data, error, loading, reload } = useApi<Overview>("/backups/overview", ctx.q, { refreshMs: 20000 });
  if (loading) return <Skeleton className="h-40 w-full" />;
  if (error || !data) return <Card><ErrorState onRetry={reload} /></Card>;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard label="Completed" value={String(data.completed)} icon={CheckCircle2} hint={`${data.running} running`} />
        <KpiCard label="Failed (7 days)" value={String(data.failed_7d)} icon={XCircle} />
        <KpiCard label="Verified" value={String(data.verified)} icon={ShieldAlert} hint={`${data.unverified} unverified · ${data.verification_problems} problems`} />
        <KpiCard label="Stored" value={fmtBytes(data.total_size_bytes)} icon={HardDrive} />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader title="Schedule" />
          <CardBody className="text-sm">
            <p className="flex items-center gap-2"><Clock className="h-4 w-4 text-muted" />{data.active_schedules} active schedule(s)</p>
            <p className="mt-1 text-muted">Next run: {fmtDate(data.next_run_at)}</p>
            <h3 className="mt-4 text-sm font-semibold text-text">Last success by type</h3>
            {Object.keys(data.last_success_by_type).length === 0 ? <p className="mt-1 text-muted">No completed backups yet.</p> : (
              <ul className="mt-1 space-y-1">{Object.entries(data.last_success_by_type).map(([t, d]) => <li key={t} className="flex justify-between gap-2"><span>{typeLabel(t)}</span><span className="tnum text-muted">{fmtDate(d)}</span></li>)}</ul>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Off-site copy" />
          <CardBody className="text-sm">
            <p className="flex items-center gap-2"><Archive className="h-4 w-4 text-muted" /><StatusPill status={data.offsite.status} /></p>
            {!data.offsite.configured && <p className="mt-2 text-muted">No off-site destination has received a backup. Backups currently exist only on the server&apos;s own storage. Configure a destination in the Destinations tab.</p>}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
