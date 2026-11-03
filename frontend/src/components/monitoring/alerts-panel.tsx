"use client";

import * as React from "react";
import { Check, CheckCheck } from "lucide-react";
import { ResourceTable } from "@/components/resource/resource-table";
import { Can, fmtDate } from "@/components/resource/helpers";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { ApiError, post } from "@/lib/http";
import type { Column } from "@/components/ui/data-table";
import { METRIC_LABELS, relTime } from "./format";
import type { AlertRow } from "./types";

const stateTone = { open: "danger", acknowledged: "warning", resolved: "success" } as const;

export function AlertsPanel({ serverId, showServer = true }: { serverId?: string; showServer?: boolean }) {
  const toast = useToast();
  const [view, setView] = React.useState<"active" | "all">("active");

  async function act(a: AlertRow, verb: "acknowledge" | "resolve", reload: () => void) {
    try {
      await post(`/monitoring/alerts/${a.id}/${verb}`);
      toast({ title: verb === "acknowledge" ? "Alert acknowledged" : "Alert resolved", tone: "success" });
      reload();
    } catch (e) {
      toast({ title: "Action failed", description: e instanceof ApiError ? e.message : "Network error", tone: "danger" });
    }
  }

  const columns: Column<AlertRow>[] = [
    { id: "sev", header: "Severity", sortValue: (r) => r.severity, cell: (r) => <Badge tone={r.severity === "critical" ? "danger" : "warning"} dot>{r.severity}</Badge> },
    {
      id: "msg",
      header: "Alert",
      cell: (r) => (
        <div className="max-w-md">
          <p className="font-medium text-text">{METRIC_LABELS[r.metric] ?? r.metric}</p>
          <p className="text-xs break-words text-muted">{r.message}{r.occurrences > 1 ? ` (seen ${r.occurrences} times)` : ""}</p>
        </div>
      ),
    },
    ...(showServer ? [{ id: "server", header: "Server", hideOnMobile: true, sortValue: (r: AlertRow) => r.server_name ?? "", cell: (r: AlertRow) => r.server_name ?? "—" } satisfies Column<AlertRow>] : []),
    { id: "state", header: "State", sortValue: (r) => r.state, cell: (r) => <Badge tone={stateTone[r.state]} dot>{r.state}</Badge> },
    { id: "opened", header: "Opened", sortValue: (r) => r.opened_at, cell: (r) => <span className="tnum" title={fmtDate(r.opened_at)}>{relTime(r.opened_at)}</span> },
    {
      id: "life",
      header: "Lifecycle",
      hideOnMobile: true,
      cell: (r) => (
        <span className="text-xs text-muted">
          {r.acknowledged_at ? `Acknowledged ${fmtDate(r.acknowledged_at)}` : "Not acknowledged"}
          {r.resolved_at ? ` · ${r.auto_resolved ? "auto-resolved" : "resolved by staff"} ${fmtDate(r.resolved_at)}` : ""}
        </span>
      ),
    },
  ];

  return (
    <ResourceTable<AlertRow>
      key={view}
      path="/monitoring/alerts"
      query={{ server_id: serverId, state: view === "active" ? "active" : undefined, page_size: 100 }}
      caption={view === "active" ? "Incidents" : "Alert history"}
      columns={columns}
      searchText={(r) => `${r.metric} ${r.message} ${r.server_name ?? ""} ${r.state}`}
      refreshMs={30000}
      emptyTitle={view === "active" ? "No active incidents" : "No alerts recorded"}
      emptyDescription="Alerts open when a metric crosses its warning or critical threshold, or when heartbeats stop."
      toolbarExtra={<Segmented label="Alert view" value={view} onChange={setView} options={[{ value: "active", label: "Active" }, { value: "all", label: "History" }]} />}
      rowActions={(r, reload) => (
        <Can perm="monitoring:write">
          <div className="flex gap-1">
            {r.state === "open" && (
              <Button size="sm" variant="outline" onClick={() => void act(r, "acknowledge", reload)}>
                <Check className="h-4 w-4" /> Acknowledge
              </Button>
            )}
            {r.state !== "resolved" && (
              <Button size="sm" variant="ghost" onClick={() => void act(r, "resolve", reload)}>
                <CheckCheck className="h-4 w-4" /> Resolve
              </Button>
            )}
          </div>
        </Can>
      )}
    />
  );
}
