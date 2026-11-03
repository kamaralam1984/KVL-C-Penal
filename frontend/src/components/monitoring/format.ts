export const fmtPct = (v?: number | null, digits = 0) => (v == null ? "—" : `${v.toFixed(digits)}%`);

export function fmtRate(bytesPerSec?: number | null): string {
  if (bytesPerSec == null) return "—";
  const u = ["B/s", "KB/s", "MB/s", "GB/s"];
  let v = bytesPerSec;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

export function fmtUptime(seconds?: number | null): string {
  if (seconds == null) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function relTime(iso?: string | null, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export const browserTz = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
};

export const METRIC_LABELS: Record<string, string> = {
  cpu_percent: "CPU utilisation",
  mem_percent: "Memory used",
  disk_percent: "Fullest disk mount",
  swap_percent: "Swap used",
  heartbeat_age_seconds: "Time since last heartbeat",
};
