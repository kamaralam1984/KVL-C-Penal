/** Every service call returns its provenance so the UI can never present fixtures as live data. */
export type DataSource = "demo" | "live";

export interface ApiResult<T> {
  data: T;
  source: DataSource;
}

export interface Kpi {
  id: string;
  label: string;
  value: string;
  unit?: string;
  /** Percent change versus the previous period. */
  delta?: number;
  deltaGoodWhen?: "up" | "down";
  hint?: string;
}

export interface TimePoint {
  t: string;
  [series: string]: number | string;
}

export interface ActivityItem {
  id: string;
  actor: string;
  action: string;
  target: string;
  time: string;
}

export interface ServerHealth {
  id: string;
  name: string;
  status: "healthy" | "warning" | "critical" | "unknown";
  cpu: number;
  memory: number;
  disk: number;
}

export interface UsageMeter {
  id: string;
  label: string;
  used: number;
  limit: number;
  unit: string;
}

export interface SimpleRow {
  id: string;
  name: string;
  detail: string;
  status: "active" | "pending" | "warning" | "suspended";
  value: string;
}
