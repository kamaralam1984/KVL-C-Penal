"use client";

import * as React from "react";
import { Area, AreaChart, CartesianGrid, Line, LineChart, ReferenceLine, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard, ChartTooltip, axisProps, useChartAnimation, type SeriesDef } from "@/components/charts/chart-container";
import { Segmented } from "@/components/ui/tabs";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { KpiCard } from "@/components/ui/kpi-card";
import { LiveBadge, fmtBytes, fmtDate } from "@/components/resource/helpers";
import { AvailabilityBadge, availabilityHint } from "@/components/servers/availability-badge";
import { useApi } from "@/hooks/use-api";
import { Activity, Cpu, HardDrive, MemoryStick } from "lucide-react";
import { browserTz, fmtPct, fmtRate, fmtUptime, relTime } from "./format";
import type { MetricsResponse, Point } from "./types";

const RANGES = [
  { value: "1h", label: "1h" },
  { value: "6h", label: "6h" },
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "90d", label: "90d" },
] as const;
type Range = (typeof RANGES)[number]["value"];

function tick(t: string, range: Range, tz: string): string {
  const d = new Date(t);
  return range === "1h" || range === "6h" || range === "24h"
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: tz })
    : d.toLocaleDateString([], { month: "short", day: "numeric", timeZone: tz });
}

type Row = Record<string, number | string | null>;

function LineCard({
  title,
  unit,
  series,
  rows,
  yMax,
  warn,
  crit,
  area,
  summary,
}: {
  title: string;
  unit: string;
  series: SeriesDef[];
  rows: Row[];
  yMax?: number;
  warn?: number;
  crit?: number;
  area?: boolean;
  summary: string;
}) {
  const anim = useChartAnimation();
  const common = { data: rows, margin: { top: 8, right: 12, bottom: 0, left: 0 } };
  const parts = [
    <CartesianGrid key="g" stroke="var(--chart-grid)" vertical={false} />,
    <XAxis key="x" dataKey="label" {...axisProps} interval="preserveStartEnd" minTickGap={32} />,
    <YAxis key="y" {...axisProps} axisLine={false} width={48} domain={[0, yMax ?? "auto"]} />,
    <Tooltip key="t" content={(p) => <ChartTooltip {...p} unit={` ${unit}`} series={series} />} cursor={{ stroke: "var(--border-strong)" }} />,
    warn !== undefined && <ReferenceLine key="w" y={warn} stroke="var(--warning)" strokeDasharray="4 4" />,
    crit !== undefined && <ReferenceLine key="c" y={crit} stroke="var(--danger)" strokeDasharray="4 4" />,
  ];
  return (
    <ChartCard title={title} unit={unit} series={series} data={rows} summary={summary}>
      {area ? (
        <AreaChart {...common}>
          {parts}
          {series.map((s) => (
            <Area key={s.key} dataKey={s.key} stroke={s.color} fill={s.color} fillOpacity={0.14} strokeWidth={2} connectNulls={false} isAnimationActive={anim} animationDuration={700} />
          ))}
        </AreaChart>
      ) : (
        <LineChart {...common}>
          {parts}
          {series.map((s, i) => (
            <Line key={s.key} dataKey={s.key} stroke={s.color} strokeWidth={2} strokeDasharray={i > 0 ? "5 4" : undefined} dot={false} connectNulls={false} isAnimationActive={anim} animationDuration={700} />
          ))}
        </LineChart>
      )}
    </ChartCard>
  );
}

const nums = (rows: Row[], k: string) => rows.map((r) => r[k]).filter((v): v is number => typeof v === "number");
const summarize = (rows: Row[], k: string, unit: string) => {
  const v = nums(rows, k);
  return v.length ? `Latest ${v[v.length - 1].toFixed(1)} ${unit}, range ${Math.min(...v).toFixed(1)} to ${Math.max(...v).toFixed(1)} ${unit}. Gaps mean no data was received.` : "No data in this range.";
};

export function MetricsPanel({ serverId, thresholds }: { serverId: string; thresholds?: Record<string, { warning: number; critical: number }> }) {
  const [range, setRange] = React.useState<Range>("24h");
  const [tz] = React.useState(browserTz);
  const { data, error, loading, reload } = useApi<MetricsResponse>(`/servers/${serverId}/metrics`, { range, tz }, { refreshMs: 60000 });
  const rows = React.useMemo<Row[]>(
    () =>
      (data?.points ?? []).map((p: Point) => ({
        label: tick(p.t, range, tz),
        cpu: p.cpu_percent,
        load1: p.load1,
        load5: p.load5,
        load15: p.load15,
        mem: p.mem_percent,
        swap: p.swap_percent,
        disk: p.disk_max_percent,
        rx: p.net_rx_bps == null ? null : +(p.net_rx_bps / 1024).toFixed(2),
        tx: p.net_tx_bps == null ? null : +(p.net_tx_bps / 1024).toFixed(2),
      })),
    [data, range, tz],
  );

  if (loading) return <Skeleton className="h-64 w-full" />;
  if (error) return <ErrorState onRetry={reload} />;
  if (!data) return null;
  const l = data.latest;
  const th = thresholds ?? {};

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <AvailabilityBadge value={data.availability} />
          <span title={fmtDate(data.last_heartbeat_at)} className="tnum">Last heartbeat {relTime(data.last_heartbeat_at)}</span>
          <LiveBadge />
        </div>
        <Segmented label="Time range" value={range} onChange={setRange} options={[...RANGES]} />
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard label="CPU" icon={Cpu} value={l?.cpu_percent == null ? "—" : l.cpu_percent.toFixed(0)} unit={l?.cpu_percent == null ? undefined : "%"} hint={l?.load1 == null ? "load n/a" : `load ${l.load1.toFixed(2)} / ${l.load5?.toFixed(2) ?? "—"} / ${l.load15?.toFixed(2) ?? "—"}`} />
        <KpiCard label="Memory" icon={MemoryStick} value={l?.mem_percent == null ? "—" : l.mem_percent.toFixed(0)} unit={l?.mem_percent == null ? undefined : "%"} hint={l?.mem_total == null ? undefined : `${fmtBytes(l.mem_used)} of ${fmtBytes(l.mem_total)}`} />
        <KpiCard label="Fullest disk" icon={HardDrive} value={l?.disk_max_percent == null ? "—" : l.disk_max_percent.toFixed(0)} unit={l?.disk_max_percent == null ? undefined : "%"} hint={l?.swap_percent == null ? "no swap configured" : `swap ${fmtPct(l.swap_percent)}`} />
        <KpiCard label="Uptime" icon={Activity} value={fmtUptime(l?.uptime_seconds)} hint={l ? `net ↓ ${fmtRate(l.net_rx_bps)} ↑ ${fmtRate(l.net_tx_bps)}` : undefined} />
      </div>

      {!data.has_data ? (
        <Card>
          <EmptyState
            title="No metrics in this range"
            description={data.availability === "unknown" ? "No agent has reported yet. Enroll the agent to start collecting metrics." : `${availabilityHint(data.availability)} Nothing is shown rather than a misleading zero.`}
          />
        </Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          <LineCard title="CPU utilisation" unit="%" yMax={100} area warn={th.cpu_percent?.warning} crit={th.cpu_percent?.critical} series={[{ key: "cpu", label: "CPU", color: "var(--chart-1)" }]} rows={rows} summary={summarize(rows, "cpu", "%")} />
          <LineCard title="Load average" unit="load" series={[{ key: "load1", label: "1 min", color: "var(--chart-1)" }, { key: "load5", label: "5 min", color: "var(--chart-2)" }, { key: "load15", label: "15 min", color: "var(--chart-3)" }]} rows={rows} summary={summarize(rows, "load1", "load")} />
          <LineCard title="Memory and swap" unit="%" yMax={100} warn={th.mem_percent?.warning} crit={th.mem_percent?.critical} series={[{ key: "mem", label: "Memory used", color: "var(--chart-2)" }, { key: "swap", label: "Swap used", color: "var(--chart-4)" }]} rows={rows} summary={summarize(rows, "mem", "%")} />
          <LineCard title="Storage (fullest mount)" unit="%" yMax={100} warn={th.disk_percent?.warning} crit={th.disk_percent?.critical} series={[{ key: "disk", label: "Disk used", color: "var(--chart-3)" }]} rows={rows} summary={summarize(rows, "disk", "%")} />
          <div className="xl:col-span-2">
            <LineCard title="Network throughput" unit="KB/s" series={[{ key: "rx", label: "Receive", color: "var(--chart-1)" }, { key: "tx", label: "Transmit", color: "var(--chart-2)" }]} rows={rows} summary={summarize(rows, "rx", "KB/s")} />
          </div>
        </div>
      )}

      <p className="text-xs text-muted">
        Times in {data.tz}. Buckets are {data.bucket_seconds >= 3600 ? `${data.bucket_seconds / 3600} h` : `${data.bucket_seconds / 60} min`} averages; gaps mean no data was received. Dashed lines mark the warning and critical thresholds.
      </p>

      {l?.disks && l.disks.length > 0 && (
        <Card>
          <CardHeader title="Disk mounts" description={`Latest sample ${relTime(l.ts)}`} />
          <CardBody className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Disk usage by mount</caption>
              <thead className="text-left text-xs text-muted"><tr><th className="py-1 pr-3">Mount</th><th className="py-1 pr-3">Used</th><th className="py-1 pr-3">Free</th><th className="py-1 pr-3">Total</th><th className="py-1">Used %</th></tr></thead>
              <tbody className="divide-y divide-border">
                {l.disks.map((d) => (
                  <tr key={d.mount}>
                    <td className="py-1.5 pr-3"><code className="text-xs">{d.mount}</code></td>
                    <td className="tnum py-1.5 pr-3">{fmtBytes(d.used)}</td>
                    <td className="tnum py-1.5 pr-3">{fmtBytes(d.free)}</td>
                    <td className="tnum py-1.5 pr-3">{fmtBytes(d.total)}</td>
                    <td className="tnum py-1.5">{fmtPct(d.percent, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {l.interfaces && l.interfaces.length > 0 && (
              <p className="mt-3 text-xs text-muted">
                Interfaces: {l.interfaces.map((i) => `${i.name} ${i.is_up == null ? "unknown" : i.is_up ? "up" : "down"}`).join(", ")}
              </p>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  );
}
