"use client";

import { Activity, Cpu, Gauge, HardDrive, MemoryStick, Network } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { BarMeter, RingGauge } from "@/components/ui/meters";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import { AccentLine } from "@/components/ui/accent-line";
import { LiveBadge, fmtBytes } from "@/components/resource/helpers";
import { AvailabilityBadge } from "@/components/servers/availability-badge";
import { useApi } from "@/hooks/use-api";
import { fmtPct, fmtRate, fmtUptime, relTime } from "./format";
import type { LatestResponse } from "./types";

type Th = Record<string, { warning: number; critical: number }>;

/** The "what is my server doing right now" view: round gauges, per-core bars, network speed, disks and the heaviest processes. */
export function LiveResources({ serverId, thresholds }: { serverId: string; thresholds?: Th }) {
  const { data, error, loading, reload } = useApi<LatestResponse>(`/servers/${serverId}/metrics/latest`, undefined, { refreshMs: 10000 });
  if (loading) return <Skeleton className="h-72 w-full" />;
  if (error) return <ErrorState onRetry={reload} />;
  const s = data?.sample;
  if (!data || !s) {
    return (
      <Card>
        <EmptyState title="No live data yet" description="No agent has reported on this server. Enroll the agent to see CPU, RAM, disk and network here." />
      </Card>
    );
  }
  const th = thresholds ?? {};
  const cores = s.cpu_cores ?? [];
  const link = Math.max(0, ...(s.interfaces ?? []).filter((i) => i.is_up && i.speed_mbps).map((i) => i.speed_mbps ?? 0)) * 125000; // Mbit/s -> bytes/s
  const rx = s.net_rx_bps;
  const tx = s.net_tx_bps;
  const swapKnown = s.swap_total != null && s.swap_total > 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
        <AvailabilityBadge value={data.availability} />
        <span className="tnum">Updated {relTime(s.ts)}</span>
        <LiveBadge />
        <span className="tnum ml-auto">Uptime {fmtUptime(s.uptime_seconds)}</span>
      </div>

      <Card className="relative overflow-hidden">
        <div className="absolute inset-x-0 top-0"><AccentLine className="!h-[2px]" /></div>
        <CardHeader title="Server health" description="Round gauges turn amber and red at your alert thresholds." />
        <CardBody>
          <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
            <RingGauge label="CPU" value={s.cpu_percent} warn={th.cpu_percent?.warning} crit={th.cpu_percent?.critical} caption={s.cpu_count ? `${s.cpu_count} cores` : undefined} />
            <RingGauge label="Memory" value={s.mem_percent} warn={th.mem_percent?.warning} crit={th.mem_percent?.critical} caption={s.mem_total ? `${fmtBytes(s.mem_used)} of ${fmtBytes(s.mem_total)}` : undefined} />
            <RingGauge label="Disk (fullest)" value={s.disk_max_percent} warn={th.disk_percent?.warning} crit={th.disk_percent?.critical} caption={s.disks?.length ? `${s.disks.length} mount${s.disks.length > 1 ? "s" : ""}` : undefined} />
            <RingGauge label="Swap" value={swapKnown ? s.swap_percent : null} warn={th.swap_percent?.warning} crit={th.swap_percent?.critical} caption={swapKnown ? `${fmtBytes(s.swap_used)} of ${fmtBytes(s.swap_total)}` : "not configured"} />
          </div>
        </CardBody>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title={<span className="flex items-center gap-2"><AnimatedIcon icon={Network} size={16} idle="float" />Network speed</span>} description={link ? `Link capacity ${(link / 125000).toLocaleString()} Mbit/s` : "Link speed not reported by the interface"} />
          <CardBody className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div><p className="text-xs text-muted">Download</p><p className="tnum text-2xl font-semibold text-text">{fmtRate(rx)}</p></div>
              <div><p className="text-xs text-muted">Upload</p><p className="tnum text-2xl font-semibold text-text">{fmtRate(tx)}</p></div>
            </div>
            {link > 0 ? (
              <div className="space-y-3">
                <BarMeter label="Download of link" value={rx == null ? null : (rx / link) * 100} right={fmtPct(rx == null ? null : (rx / link) * 100, 2)} color="var(--chart-1)" />
                <BarMeter label="Upload of link" value={tx == null ? null : (tx / link) * 100} right={fmtPct(tx == null ? null : (tx / link) * 100, 2)} color="var(--chart-2)" />
              </div>
            ) : (
              <p className="text-xs text-muted">Bars need the link capacity, which this interface does not report. Rates above are still live.</p>
            )}
            {s.interfaces && s.interfaces.length > 0 && (
              <ul className="divide-y divide-border text-sm">
                {s.interfaces.filter((i) => !i.name.startsWith("lo")).slice(0, 6).map((i) => (
                  <li key={i.name} className="flex items-center justify-between gap-2 py-1.5">
                    <span className="flex items-center gap-2"><code className="text-xs">{i.name}</code><span className={i.is_up ? "text-success" : "text-muted"}>{i.is_up == null ? "unknown" : i.is_up ? "up" : "down"}</span></span>
                    <span className="tnum text-xs text-muted">↓ {fmtRate(i.rx_bytes_per_s)} · ↑ {fmtRate(i.tx_bytes_per_s)}{i.speed_mbps ? ` · ${i.speed_mbps} Mbit/s` : ""}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title={<span className="flex items-center gap-2"><AnimatedIcon icon={Cpu} size={16} idle="float" />CPU cores and load</span>} description={s.load1 == null ? "Load average not available" : `Load ${s.load1.toFixed(2)} / ${s.load5?.toFixed(2) ?? "—"} / ${s.load15?.toFixed(2) ?? "—"} (1, 5, 15 min)`} />
          <CardBody className="space-y-4">
            {cores.length > 0 ? (
              <div className="grid grid-cols-2 gap-x-4 gap-y-2.5">
                {cores.map((c, i) => <BarMeter key={i} label={`Core ${i + 1}`} value={c} warn={th.cpu_percent?.warning} crit={th.cpu_percent?.critical} />)}
              </div>
            ) : (
              <p className="text-xs text-muted">Per-core usage was not reported by this agent.</p>
            )}
            {s.cpu_count && s.load1 != null && (
              <div className="space-y-2 border-t border-border pt-3">
                <p className="text-xs text-muted">Load against {s.cpu_count} cores (100% means every core is busy on average)</p>
                <BarMeter label="1 min" value={(s.load1 / s.cpu_count) * 100} warn={80} crit={100} />
                <BarMeter label="5 min" value={s.load5 == null ? null : (s.load5 / s.cpu_count) * 100} warn={80} crit={100} />
                <BarMeter label="15 min" value={s.load15 == null ? null : (s.load15 / s.cpu_count) * 100} warn={80} crit={100} />
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title={<span className="flex items-center gap-2"><AnimatedIcon icon={HardDrive} size={16} idle="float" />Storage</span>} description="Used space per mount" />
          <CardBody className="space-y-3">
            {(s.disks ?? []).length === 0 && <p className="text-sm text-muted">No mounts reported.</p>}
            {(s.disks ?? []).map((d) => (
              <BarMeter key={d.mount} label={<><code className="text-xs">{d.mount}</code>{d.fstype ? <span className="ml-2 text-faint">{d.fstype}</span> : null}</>} value={d.percent} warn={th.disk_percent?.warning} crit={th.disk_percent?.critical} right={`${fmtBytes(d.used)} / ${fmtBytes(d.total)} · ${fmtPct(d.percent, 1)}`} />
            ))}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title={<span className="flex items-center gap-2"><AnimatedIcon icon={MemoryStick} size={16} idle="float" />Heaviest processes</span>} description="By RAM. Names only; command lines are never collected." />
          <CardBody className="space-y-3">
            {(s.processes ?? []).length === 0 && <p className="text-sm text-muted">The agent did not report processes.</p>}
            {(s.processes ?? []).map((p) => (
              <BarMeter key={p.pid} label={<><span className="text-text">{p.name}</span><span className="ml-2 text-faint">pid {p.pid}</span></>} value={s.mem_total && p.rss_bytes != null ? (p.rss_bytes / s.mem_total) * 100 : null} color="var(--chart-3)" right={`${fmtBytes(p.rss_bytes)}${p.cpu_percent != null ? ` · ${p.cpu_percent.toFixed(0)}% CPU` : ""}`} />
            ))}
          </CardBody>
        </Card>
      </div>
      <p className="flex items-center gap-1.5 text-xs text-muted"><Activity className="h-3.5 w-3.5" aria-hidden /><Gauge className="h-3.5 w-3.5" aria-hidden />The agent reports every 30 seconds; this view refreshes every 10 seconds.</p>
    </div>
  );
}
