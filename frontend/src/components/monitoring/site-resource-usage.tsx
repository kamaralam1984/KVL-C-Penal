"use client";

import * as React from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard, ChartTooltip, axisProps, useChartAnimation } from "@/components/charts/chart-container";
import { RingGauge } from "@/components/ui/meters";
import { Skeleton } from "@/components/ui/skeleton";
import { fmtBytes } from "@/components/resource/helpers";
import { useApi } from "@/hooks/use-api";
import { browserTz } from "./format";
import type { SitePoint, SiteUsage } from "./types";

const mb = (b: number | null) => (b == null ? null : +(b / 1048576).toFixed(2));

/** One website's own RAM, CPU, disk and data usage, as last reported by the agent. Scoped by the API to the signed-in tenant. */
export function SiteResourceUsage({ websiteId, diskQuotaMb }: { websiteId: string; diskQuotaMb?: number | null }) {
  const live = useApi<{ reported: boolean; reason: string | null; item: SiteUsage | null }>(`/websites/${websiteId}/usage/live`, undefined, { refreshMs: 30000 });
  const hist = useApi<{ points: SitePoint[] }>(`/websites/${websiteId}/usage/history`, { range: "24h" }, { refreshMs: 120000 });
  const anim = useChartAnimation();
  const rows = React.useMemo(
    () => (hist.data?.points ?? []).map((p) => ({ label: new Date(p.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: browserTz() }), ram: mb(p.rss_bytes), out: mb(p.bytes_out) })),
    [hist.data],
  );
  if (live.loading) return <Skeleton className="h-40 w-full" />;
  const it = live.data?.item;
  if (!it) {
    return <p className="text-sm text-muted">{live.data?.reason === "not_assigned_to_a_server" ? "This website is not on a server yet." : "No usage reported yet. Figures appear after the site is live and the server agent has reported."}</p>;
  }
  const diskPct = it.disk_bytes != null && diskQuotaMb ? (it.disk_bytes / (diskQuotaMb * 1048576)) * 100 : null;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 items-start gap-4 sm:grid-cols-4">
        <RingGauge size={104} label="Disk" value={diskPct} caption={diskQuotaMb ? `${fmtBytes(it.disk_bytes)} of ${diskQuotaMb} MB` : `${fmtBytes(it.disk_bytes)} (no quota set)`} />
        <div className="text-center"><p className="tnum text-2xl font-semibold text-text">{fmtBytes(it.rss_bytes)}</p><p className="text-sm font-medium text-text">RAM now</p><p className="text-xs text-muted">{it.procs ? `${it.procs} process${it.procs > 1 ? "es" : ""}` : "nothing running"}</p></div>
        <div className="text-center"><p className="tnum text-2xl font-semibold text-text">{it.cpu_percent == null ? "—" : `${it.cpu_percent.toFixed(1)}%`}</p><p className="text-sm font-medium text-text">CPU now</p><p className="text-xs text-muted">of one core</p></div>
        <div className="text-center"><p className="tnum text-2xl font-semibold text-text">{fmtBytes(it.bytes_out_24h)}</p><p className="text-sm font-medium text-text">Data sent, 24 h</p><p className="tnum text-xs text-muted">{it.requests_24h == null ? "no access log yet" : `${it.requests_24h.toLocaleString()} requests`}</p></div>
      </div>
      {rows.length >= 2 && (
        <div className="grid gap-3 xl:grid-cols-2">
          <ChartCard title="RAM, last 24 h" unit="MB" series={[{ key: "ram", label: "RAM", color: "var(--chart-1)" }]} data={rows} height={180} summary={`Latest RAM ${rows[rows.length - 1].ram ?? "unknown"} MB.`}>
            <AreaChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
              <XAxis dataKey="label" {...axisProps} interval="preserveStartEnd" minTickGap={32} />
              <YAxis {...axisProps} axisLine={false} width={40} />
              <Tooltip content={(p) => <ChartTooltip {...p} unit=" MB" series={[{ key: "ram", label: "RAM", color: "var(--chart-1)" }]} />} />
              <Area dataKey="ram" stroke="var(--chart-1)" fill="var(--chart-1)" fillOpacity={0.16} strokeWidth={2} connectNulls={false} isAnimationActive={anim} animationDuration={800} />
            </AreaChart>
          </ChartCard>
          <ChartCard title="Data sent per 5 min" unit="MB" series={[{ key: "out", label: "Data out", color: "var(--gold)" }]} data={rows} height={180} summary="Bytes sent to visitors per 5-minute interval.">
            <BarChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
              <XAxis dataKey="label" {...axisProps} interval="preserveStartEnd" minTickGap={32} />
              <YAxis {...axisProps} axisLine={false} width={40} />
              <Tooltip content={(p) => <ChartTooltip {...p} unit=" MB" series={[{ key: "out", label: "Data out", color: "var(--gold)" }]} />} cursor={{ fill: "var(--subtle)" }} />
              <Bar dataKey="out" fill="var(--gold)" radius={[3, 3, 0, 0]} isAnimationActive={anim} animationDuration={800} />
            </BarChart>
          </ChartCard>
        </div>
      )}
    </div>
  );
}
