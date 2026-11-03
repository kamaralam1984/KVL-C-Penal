import type { Availability } from "@/components/servers/types";

export interface Point {
  t: string;
  cpu_percent: number | null;
  load1: number | null;
  load5: number | null;
  load15: number | null;
  mem_percent: number | null;
  swap_percent: number | null;
  disk_max_percent: number | null;
  net_rx_bps: number | null;
  net_tx_bps: number | null;
}

export interface Mount {
  mount: string;
  fstype?: string | null;
  total: number | null;
  used: number | null;
  free: number | null;
  percent: number | null;
}

export interface Latest {
  ts: string;
  cpu_percent: number | null;
  load1: number | null;
  load5: number | null;
  load15: number | null;
  mem_total: number | null;
  mem_used: number | null;
  mem_percent: number | null;
  swap_total: number | null;
  swap_used: number | null;
  swap_percent: number | null;
  disk_max_percent: number | null;
  net_rx_bps: number | null;
  net_tx_bps: number | null;
  uptime_seconds: number | null;
  disks: Mount[] | null;
  interfaces: { name: string; is_up: boolean | null; speed_mbps: number | null; rx_bytes_per_s?: number | null; tx_bytes_per_s?: number | null }[] | null;
  cpu_cores: number[] | null;
  cpu_count: number | null;
  processes: { pid: number; name: string; rss_bytes: number | null; cpu_percent: number | null }[] | null;
}

export interface LatestResponse {
  availability: Availability;
  last_heartbeat_at: string | null;
  sample: Latest | null;
}

export interface SiteUsage {
  name: string;
  kind: "site" | "app";
  label: string;
  owner: { kind: string; id: string; name: string; tenant_id: string; tenant: string } | null;
  sampled_at: string;
  stale: boolean;
  disk_bytes: number | null;
  rss_bytes: number | null;
  cpu_percent: number | null;
  procs: number | null;
  bytes_out_total: number | null;
  requests_total: number | null;
  bytes_out_24h: number | null;
  requests_24h: number | null;
  errors_4xx_24h: number | null;
  errors_5xx_24h: number | null;
}

export interface SitePoint {
  t: string;
  rss_bytes: number | null;
  cpu_percent: number | null;
  disk_bytes: number | null;
  bytes_out: number | null;
  requests: number | null;
}

export interface MetricsResponse {
  server_id: string;
  range: string;
  tz: string;
  bucket_seconds: number;
  availability: Availability;
  last_heartbeat_at: string | null;
  has_data: boolean;
  points: Point[];
  latest: Latest | null;
}

export interface ServiceRow {
  name: string;
  state: "running" | "stopped" | "failed" | "not_configured";
  detail: string | null;
  checked_at: string | null;
  stale: boolean;
}

export interface AlertRow {
  id: string;
  server_id: string;
  server_name: string | null;
  metric: string;
  severity: "warning" | "critical";
  state: "open" | "acknowledged" | "resolved";
  value: number | null;
  threshold: number | null;
  message: string;
  occurrences: number;
  opened_at: string;
  last_seen_at: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
  auto_resolved: boolean;
}

export interface ThresholdRow {
  metric: string;
  label: string;
  unit: string;
  warning: number;
  critical: number;
  cooldown_seconds: number;
  enabled: boolean;
  customized: boolean;
}

export interface OverviewServer {
  id: string;
  name: string;
  hostname: string | null;
  availability: Availability;
  agent_version: string | null;
  last_heartbeat_at: string | null;
  cpu_percent: number | null;
  mem_percent: number | null;
  disk_percent: number | null;
  uptime_seconds: number | null;
  services: { running: number; stopped: number; not_configured: number };
  open_alerts: { warning: number; critical: number };
}

export interface Overview {
  generated_at: string;
  totals: { servers: number; connected: number; stale: number; disconnected: number; unknown: number; disabled: number; open_alerts: number; critical_alerts: number };
  servers: OverviewServer[];
  policy: { heartbeat_interval_seconds: number; stale_after_seconds: number; disconnected_after_seconds: number };
}
