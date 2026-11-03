"use client";

import * as React from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, Tooltip, XAxis, YAxis } from "recharts";
import { Globe } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { BarMeter } from "@/components/ui/meters";
import { Segmented } from "@/components/ui/tabs";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import { ChartCard, ChartTooltip, axisProps, useChartAnimation } from "@/components/charts/chart-container";
import { LiveBadge, fmtBytes } from "@/components/resource/helpers";
import { useApi } from "@/hooks/use-api";
import { browserTz } from "./format";
import type { SitePoint, SiteUsage } from "./types";

const PALETTE = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--gold)", "var(--silver)"];
const RANGES = [{ value: "6h", label: "6h" }, { value: "24h", label: "24h" }, { value: "7d", label: "7d" }, { value: "30d", label: "30d" }] as const;
type Range = (typeof RANGES)[number]["value"];

const sum = (xs: (number | null)[]) => xs.reduce<number>((a, b) => a + (b ?? 0), 0);
const maxOf = (xs: (number | null)[]) => Math.max(0, ...xs.map((x) => x ?? 0));
const mb = (b: number | null) => (b == null ? null : +(b / 1048576).toFixed(2));

function Donut({ title, unit, rows, fmt }: { title: string; unit: string; rows: { name: string; value: number }[]; fmt: (n: number) => string }) {
  const anim = useChartAnimation();
  const total = sum(rows.map((r) => r.value));
  return (
    <ChartCard title={title} unit={unit} series={rows.map((r, i) => ({ key: r.name, label: r.name, color: PALETTE[i % PALETTE.length] }))} data={rows} height={220} summary={rows.length ? `Total ${fmt(total)}. Largest: ${rows[0].name} at ${fmt(rows[0].value)}.` : "No data."}>
      <PieChart>
        <Tooltip content={(p) => <ChartTooltip {...p} series={[]} unit="" />} />
        <Pie data={rows} dataKey="value" nameKey="name" innerRadius="58%" outerRadius="88%" paddingAngle={2} stroke="var(--surface)" isAnimationActive={anim} animationDuration={900}>
          {rows.map((r, i) => <Cell key={r.name} fill={PALETTE[i % PALETTE.length]} />)}
        </Pie>
      </PieChart>
    </ChartCard>
  );
}

function SiteDetail({ serverId, site }: { serverId: string; site: SiteUsage }) {
  const [range, setRange] = React.useState<Range>("24h");
  const anim = useChartAnimation();
  const { data, error, loading, reload } = useApi<{ points: SitePoint[]; bucket_seconds: number }>(`/servers/${serverId}/sites-usage/${site.name}/series`, { range }, { refreshMs: 60000 });
  const rows = React.useMemo(
    () => (data?.points ?? []).map((p) => ({ label: new Date(p.t).toLocaleString([], { hour: "2-digit", minute: "2-digit", day: range === "6h" || range === "24h" ? undefined : "numeric", month: range === "6h" || range === "24h" ? undefined : "short", timeZone: browserTz() }), ram: mb(p.rss_bytes), cpu: p.cpu_percent, out: mb(p.bytes_out), req: p.requests })),
    [data, range],
  );
  return (
    <Card>
      <CardHeader title={`${site.label} over time`} description={site.owner ? `Owner: ${site.owner.tenant}` : site.name} actions={<Segmented label="Time range" value={range} onChange={setRange} options={[...RANGES]} />} />
      <CardBody>
        {loading ? <Skeleton className="h-56 w-full" /> : error ? <ErrorState onRetry={reload} /> : rows.length < 2 ? (
          <EmptyState title="Not enough history yet" description="Samples arrive every 5 minutes. Charts appear after the second one." className="py-6" />
        ) : (
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard title="RAM and CPU" series={[{ key: "ram", label: "RAM (MB)", color: "var(--chart-1)" }, { key: "cpu", label: "CPU (%)", color: "var(--chart-2)" }]} data={rows} height={220} summary={`Latest RAM ${rows[rows.length - 1].ram ?? "unknown"} MB.`}>
              <AreaChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                <XAxis dataKey="label" {...axisProps} interval="preserveStartEnd" minTickGap={32} />
                <YAxis {...axisProps} axisLine={false} width={44} />
                <Tooltip content={(p) => <ChartTooltip {...p} series={[{ key: "ram", label: "RAM (MB)", color: "var(--chart-1)" }, { key: "cpu", label: "CPU (%)", color: "var(--chart-2)" }]} />} />
                <Area dataKey="ram" stroke="var(--chart-1)" fill="var(--chart-1)" fillOpacity={0.16} strokeWidth={2} connectNulls={false} isAnimationActive={anim} animationDuration={800} />
                <Area dataKey="cpu" stroke="var(--chart-2)" fill="var(--chart-2)" fillOpacity={0.1} strokeWidth={2} connectNulls={false} isAnimationActive={anim} animationDuration={800} />
              </AreaChart>
            </ChartCard>
            <ChartCard title="Data sent per interval" unit="MB" series={[{ key: "out", label: "Data out", color: "var(--gold)" }]} data={rows} height={220} summary="Bytes sent to visitors in each 5-minute bucket.">
              <BarChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                <XAxis dataKey="label" {...axisProps} interval="preserveStartEnd" minTickGap={32} />
                <YAxis {...axisProps} axisLine={false} width={44} />
                <Tooltip content={(p) => <ChartTooltip {...p} unit=" MB" series={[{ key: "out", label: "Data out", color: "var(--gold)" }]} />} cursor={{ fill: "var(--subtle)" }} />
                <Bar dataKey="out" fill="var(--gold)" radius={[3, 3, 0, 0]} isAnimationActive={anim} animationDuration={800} />
              </BarChart>
            </ChartCard>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

/** Which website uses how much RAM, CPU, disk and bandwidth on a server. */
export function SitesUsage({ serverId }: { serverId: string }) {
  const { data, error, loading, reload } = useApi<{ items: SiteUsage[] }>(`/servers/${serverId}/sites-usage`, undefined, { refreshMs: 30000 });
  const [picked, setPicked] = React.useState<string | null>(null);
  const anim = useChartAnimation();
  if (loading) return <Skeleton className="h-72 w-full" />;
  if (error) return <ErrorState onRetry={reload} />;
  const items = data?.items ?? [];
  if (items.length === 0) {
    return (
      <Card>
        <EmptyState title="No website usage reported yet" description="The agent reports per-website RAM, CPU, disk and data once a website is live on this server. Nothing is shown rather than made-up numbers." />
      </Card>
    );
  }
  const selected = items.find((i) => i.name === picked) ?? items[0];
  const ramMax = maxOf(items.map((i) => i.rss_bytes));
  const diskMax = maxOf(items.map((i) => i.disk_bytes));
  const outMax = maxOf(items.map((i) => i.bytes_out_24h));
  const top = (key: "rss_bytes" | "disk_bytes") => items.filter((i) => i[key]).slice().sort((a, b) => (b[key] ?? 0) - (a[key] ?? 0)).slice(0, 7).map((i) => ({ name: i.label, value: i[key] as number }));
  const transfer = items.filter((i) => i.bytes_out_24h != null).slice().sort((a, b) => (b.bytes_out_24h ?? 0) - (a.bytes_out_24h ?? 0)).slice(0, 8).map((i) => ({ name: i.label, mb: mb(i.bytes_out_24h) }));

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm text-muted"><LiveBadge /><span>{items.length} website{items.length > 1 ? "s" : ""} and apps. RAM and CPU count processes running inside each site&apos;s folder; data comes from its nginx access log.</span></div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Donut title="RAM by website" unit="MB" rows={top("rss_bytes").map((r) => ({ ...r, value: +(r.value / 1048576).toFixed(1) }))} fmt={(n) => `${n.toFixed(1)} MB`} />
        <Donut title="Disk by website" unit="MB" rows={top("disk_bytes").map((r) => ({ ...r, value: +(r.value / 1048576).toFixed(1) }))} fmt={(n) => `${n.toFixed(1)} MB`} />
        <ChartCard title="Data sent, last 24 h" unit="MB" series={[{ key: "mb", label: "Data out", color: "var(--gold)" }]} data={transfer} height={220} summary={transfer.length ? `Most data: ${transfer[0].name}, ${transfer[0].mb} MB.` : "No data."}>
          <BarChart data={transfer} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 8 }}>
            <CartesianGrid stroke="var(--chart-grid)" horizontal={false} />
            <XAxis type="number" {...axisProps} />
            <YAxis type="category" dataKey="name" {...axisProps} axisLine={false} width={96} />
            <Tooltip content={(p) => <ChartTooltip {...p} unit=" MB" series={[{ key: "mb", label: "Data out", color: "var(--gold)" }]} />} cursor={{ fill: "var(--subtle)" }} />
            <Bar dataKey="mb" fill="var(--gold)" radius={[0, 3, 3, 0]} barSize={16} isAnimationActive={anim} animationDuration={800} />
          </BarChart>
        </ChartCard>
      </div>

      <Card>
        <CardHeader title={<span className="flex items-center gap-2"><AnimatedIcon icon={Globe} size={16} idle="float" />Per-website usage</span>} description="Select a row to see its history below." />
        <CardBody className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <caption className="sr-only">Resource usage per website</caption>
            <thead className="text-left text-xs text-muted">
              <tr><th className="py-1 pr-3">Website</th><th className="py-1 pr-3">RAM</th><th className="py-1 pr-3">CPU</th><th className="py-1 pr-3">Disk</th><th className="py-1 pr-3">Data 24 h</th><th className="py-1">Requests 24 h</th></tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((i) => (
                <tr key={i.name} className={selected.name === i.name ? "bg-subtle/60" : undefined}>
                  <td className="py-2 pr-3">
                    <button type="button" onClick={() => setPicked(i.name)} className="text-left font-medium text-text hover:underline" aria-pressed={selected.name === i.name}>{i.label}</button>
                    <span className="block text-xs text-muted">{i.owner?.tenant ?? i.name}{i.stale ? " · stale" : ""}</span>
                  </td>
                  <td className="w-40 py-2 pr-3"><BarMeter value={i.rss_bytes == null || !ramMax ? null : (i.rss_bytes / ramMax) * 100} right={fmtBytes(i.rss_bytes)} color="var(--chart-1)" /></td>
                  <td className="tnum py-2 pr-3">{i.cpu_percent == null ? "—" : `${i.cpu_percent.toFixed(1)}%`}</td>
                  <td className="w-40 py-2 pr-3"><BarMeter value={i.disk_bytes == null || !diskMax ? null : (i.disk_bytes / diskMax) * 100} right={fmtBytes(i.disk_bytes)} color="var(--chart-3)" /></td>
                  <td className="w-40 py-2 pr-3"><BarMeter value={i.bytes_out_24h == null || !outMax ? null : (i.bytes_out_24h / outMax) * 100} right={fmtBytes(i.bytes_out_24h)} color="var(--gold)" /></td>
                  <td className="tnum py-2">{i.requests_24h == null ? "—" : i.requests_24h.toLocaleString()}{i.errors_5xx_24h ? <span className="ml-2 text-xs text-danger">{i.errors_5xx_24h} errors</span> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-muted">A dash means the agent could not measure it (for example no process running, or no access log yet), not zero. Bars compare websites with each other.</p>
        </CardBody>
      </Card>

      <SiteDetail key={selected.name} serverId={serverId} site={selected} />
    </div>
  );
}
