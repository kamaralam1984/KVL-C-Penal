"use client";

import { Badge, type Tone } from "@/components/ui/badge";
import { Card, CardHeader } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useApi } from "@/hooks/use-api";
import { relTime } from "./format";
import type { ServiceRow } from "./types";

const META: Record<ServiceRow["state"], { tone: Tone; label: string }> = {
  running: { tone: "success", label: "Running" },
  stopped: { tone: "warning", label: "Stopped" },
  failed: { tone: "danger", label: "Failed" },
  not_configured: { tone: "neutral", label: "Not configured" },
};
const NAMES: Record<string, string> = { nginx: "Nginx", "php-fpm": "PHP-FPM", mariadb: "MariaDB", redis: "Redis", pm2: "PM2" };

export function ServicesList({ serverId }: { serverId: string }) {
  const { data, error, loading, reload } = useApi<ServiceRow[]>(`/servers/${serverId}/services`, undefined, { refreshMs: 60000 });
  return (
    <Card>
      <CardHeader title="Service health" description="Services that are not installed show Not configured, never Failed." />
      {loading && <Skeleton className="m-5 h-24" />}
      {error && <ErrorState onRetry={reload} />}
      {data && (
        <ul className="divide-y divide-border">
          {data.map((s) => {
            const m = META[s.state] ?? META.not_configured;
            return (
              <li key={s.name} className="flex items-center justify-between gap-3 px-5 py-3">
                <div className="min-w-0">
                  <p className="font-medium text-text">{NAMES[s.name] ?? s.name}</p>
                  <p className="truncate text-xs text-muted">{s.detail ?? (s.checked_at ? "OK" : "")}</p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-0.5">
                  <Badge tone={m.tone} dot>{m.label}</Badge>
                  <span className="tnum text-[11px] text-muted">{s.checked_at ? `checked ${relTime(s.checked_at)}${s.stale ? " (stale)" : ""}` : "never checked"}</span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
