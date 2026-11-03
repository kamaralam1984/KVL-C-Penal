"use client";

import * as React from "react";
import Link from "next/link";
import { AlertTriangle, Server as ServerIcon, ShieldCheck, WifiOff } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { RequirePerm, LiveBadge, fmtDate } from "@/components/resource/helpers";
import { AvailabilityBadge } from "@/components/servers/availability-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { KpiCard } from "@/components/ui/kpi-card";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApi } from "@/hooks/use-api";
import { cn } from "@/lib/cn";
import { AlertsPanel } from "./alerts-panel";
import { fmtPct, fmtUptime, relTime } from "./format";
import { ServerMonitor } from "./server-monitor";
import type { Overview, OverviewServer } from "./types";

function ServerCard({ s, selected, onSelect }: { s: OverviewServer; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn("min-h-11 w-full rounded-lg border bg-surface p-4 text-left shadow-sm transition-colors hover:bg-subtle", selected ? "border-primary" : "border-border")}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-medium text-text">{s.name}</p>
          <p className="truncate text-xs text-muted">{s.hostname ?? "hostname not reported"} · v{s.agent_version ?? "?"}</p>
        </div>
        <AvailabilityBadge value={s.availability} />
      </div>
      <dl className="tnum mt-3 grid grid-cols-4 gap-2 text-xs">
        {([["CPU", fmtPct(s.cpu_percent)], ["RAM", fmtPct(s.mem_percent)], ["Disk", fmtPct(s.disk_percent)], ["Up", fmtUptime(s.uptime_seconds)]] as const).map(([k, v]) => (
          <div key={k}>
            <dt className="text-muted">{k}</dt>
            <dd className="font-medium text-text">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <span title={fmtDate(s.last_heartbeat_at)}>Heartbeat {relTime(s.last_heartbeat_at)}</span>
        <span>{s.services.running} running · {s.services.not_configured} not configured</span>
        {s.open_alerts.critical > 0 && <Badge tone="danger" dot>{s.open_alerts.critical} critical</Badge>}
        {s.open_alerts.warning > 0 && <Badge tone="warning" dot>{s.open_alerts.warning} warning</Badge>}
      </p>
    </button>
  );
}

export function MonitoringDashboard() {
  const { data, error, loading, reload } = useApi<Overview>("/monitoring/overview", undefined, { refreshMs: 30000 });
  const [picked, setPicked] = React.useState<string | null>(null);
  const selected = picked ?? data?.servers[0]?.id ?? null;
  const t = data?.totals;

  return (
    <RequirePerm perm="monitoring:read">
      <PageHeader
        title="Monitoring"
        description="Live health of your hosting servers from authenticated agent heartbeats. Nothing is estimated: missing data is shown as missing."
        actions={<LiveBadge />}
      />
      {loading && <Skeleton className="h-40 w-full" />}
      {error && <ErrorState onRetry={reload} title={error.status === 403 ? "Monitoring is restricted to platform staff" : undefined} description={error.status === 403 ? "Your account cannot view server infrastructure." : undefined} />}
      {data && t && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <KpiCard label="Servers" icon={ServerIcon} value={String(t.servers)} hint={`${t.connected} connected`} />
            <KpiCard label="Not reporting" icon={WifiOff} value={String(t.stale + t.disconnected + t.unknown)} hint={`${t.stale} stale · ${t.disconnected} disconnected · ${t.unknown} unknown`} />
            <KpiCard label="Open alerts" icon={AlertTriangle} value={String(t.open_alerts)} hint={`${t.critical_alerts} critical`} />
            <KpiCard label="Heartbeat policy" icon={ShieldCheck} value={`${data.policy.heartbeat_interval_seconds}`} unit="s" hint={`stale after ${data.policy.stale_after_seconds}s, disconnected after ${data.policy.disconnected_after_seconds}s`} />
          </div>

          {data.servers.length === 0 ? (
            <Card>
              <EmptyState
                title="No servers registered"
                description="Register a server and enroll its agent to see live metrics here."
                action={<Button asChild><Link href="/admin/servers">Go to Servers</Link></Button>}
              />
            </Card>
          ) : (
            <>
              <section aria-label="Servers" className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {data.servers.map((s) => (
                  <ServerCard key={s.id} s={s} selected={s.id === selected} onSelect={() => setPicked(s.id)} />
                ))}
              </section>
              {selected && (
                <Card className="p-4">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-lg font-semibold text-text">{data.servers.find((s) => s.id === selected)?.name}</h2>
                    <Button asChild size="sm" variant="outline"><Link href={`/admin/servers/${selected}`}>Open server</Link></Button>
                  </div>
                  <ServerMonitor serverId={selected} />
                </Card>
              )}
            </>
          )}

          <Tabs defaultValue="incidents" className="space-y-3">
            <TabsList>
              <TabsTrigger value="incidents">Recent incidents</TabsTrigger>
            </TabsList>
            <TabsContent value="incidents">
              <Card>
                <CardHeader title="Incidents and alert history" description="Switch between active incidents and the full history; acknowledge to take ownership." />
                <AlertsPanel />
              </Card>
            </TabsContent>
          </Tabs>
        </div>
      )}
    </RequirePerm>
  );
}
