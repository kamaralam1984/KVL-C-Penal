"use client";

import * as React from "react";
import { Can } from "@/components/resource/helpers";
import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { useApi } from "@/hooks/use-api";
import { ApiError, put } from "@/lib/http";
import type { ThresholdRow } from "./types";

function Row({ serverId, row, onSaved }: { serverId: string; row: ThresholdRow; onSaved: (rows: ThresholdRow[]) => void }) {
  const toast = useToast();
  const [v, setV] = React.useState({ warning: String(row.warning), critical: String(row.critical), cooldown: String(row.cooldown_seconds), enabled: row.enabled });
  const [pending, setPending] = React.useState(false);
  const [err, setErr] = React.useState<string>();
  async function save() {
    setPending(true);
    setErr(undefined);
    try {
      const rows = await put<ThresholdRow[]>("/monitoring/thresholds", { server_id: serverId, metric: row.metric, warning: Number(v.warning), critical: Number(v.critical), cooldown_seconds: Number(v.cooldown), enabled: v.enabled });
      onSaved(rows);
      toast({ title: `${row.label} thresholds saved`, tone: "success" });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Network error");
    } finally {
      setPending(false);
    }
  }
  const id = `th-${row.metric}`;
  return (
    <li className="space-y-2 px-5 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-medium text-text">
          {row.label} <span className="text-xs font-normal text-muted">({row.unit}){row.customized ? " · customised" : " · default"}</span>
        </p>
        <label className="flex items-center gap-2 text-sm text-text">
          <input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" checked={v.enabled} onChange={(e) => setV({ ...v, enabled: e.target.checked })} />
          Enabled
        </label>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {([["warning", `Warning (${row.unit})`], ["critical", `Critical (${row.unit})`], ["cooldown", "Cooldown (s)"]] as const).map(([k, label]) => (
          <div key={k} className="space-y-1">
            <label htmlFor={`${id}-${k}`} className="text-xs text-muted">{label}</label>
            <Input id={`${id}-${k}`} type="number" inputMode="decimal" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} className="tnum" />
          </div>
        ))}
      </div>
      {err && <p role="alert" className="text-xs text-danger">{err}</p>}
      <Can perm="monitoring:write">
        <Button size="sm" variant="outline" loading={pending} onClick={() => void save()}>Save</Button>
      </Can>
    </li>
  );
}

export function ThresholdsEditor({ serverId }: { serverId: string }) {
  const { data, error, loading, reload } = useApi<ThresholdRow[]>("/monitoring/thresholds", { server_id: serverId });
  const [override, setOverride] = React.useState<ThresholdRow[] | null>(null);
  const rows = override ?? data;
  return (
    <Card>
      <CardHeader title="Alert thresholds" description="An alert opens at the warning level and escalates at critical. The cooldown prevents a resolved alert from re-opening immediately." />
      {loading && <Skeleton className="m-5 h-32" />}
      {error && <ErrorState onRetry={reload} />}
      {rows && (
        <ul className="divide-y divide-border">
          {rows.map((r) => (
            <Row key={`${r.metric}-${r.warning}-${r.critical}-${r.cooldown_seconds}-${r.enabled}`} serverId={serverId} row={r} onSaved={setOverride} />
          ))}
        </ul>
      )}
    </Card>
  );
}
