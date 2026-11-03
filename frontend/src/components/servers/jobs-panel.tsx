"use client";

import * as React from "react";
import { Eye, Play, XCircle } from "lucide-react";
import { ResourceTable } from "@/components/resource/resource-table";
import { FormDialog, type FieldDef } from "@/components/resource/form-dialog";
import { Can, DetailList, StatusPill, fmtDate } from "@/components/resource/helpers";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { useApi } from "@/hooks/use-api";
import { ApiError, post } from "@/lib/http";
import type { Column, FilterDef } from "@/components/ui/data-table";
import type { JobRow } from "./types";

interface Operation {
  name: string;
  timeout_seconds: number;
  destructive: boolean;
}

const STATUSES = ["queued", "running", "succeeded", "failed", "timed_out", "cancelled"];

function duration(j: JobRow): string {
  if (!j.started_at || !j.finished_at) return "—";
  const ms = new Date(j.finished_at).getTime() - new Date(j.started_at).getTime();
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function JobDetail({ id, onClose, onChanged }: { id: string | null; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const { data, error, loading, reload } = useApi<JobRow>(id ? `/jobs/${id}` : null);
  const [confirm, setConfirm] = React.useState(false);
  const cancellable = data && (data.status === "queued" || data.status === "running");
  async function cancel() {
    try {
      await post(`/jobs/${id}/cancel`);
      toast({ title: "Job cancelled", tone: "success" });
      reload();
      onChanged();
    } catch (e) {
      toast({ title: "Could not cancel", description: e instanceof ApiError ? e.message : "Network error", tone: "danger" });
    }
  }
  return (
    <Sheet open={id !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent title="Operation detail" description={data?.operation}>
        <div className="space-y-4 p-5">
          {loading && <Skeleton className="h-40 w-full" />}
          {error && <ErrorState onRetry={reload} />}
          {data && (
            <>
              <DetailList
                items={[
                  ["Operation", <code key="o">{data.operation}</code>],
                  ["Status", <StatusPill key="s" status={data.status} />],
                  ["Attempts", data.attempts],
                  ["Timeout", `${data.timeout_seconds} s`],
                  ["Queued", fmtDate(data.created_at)],
                  ["Started", fmtDate(data.started_at)],
                  ["Finished", fmtDate(data.finished_at)],
                  ["Duration", duration(data)],
                ]}
              />
              {data.error && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm break-words text-danger">{data.error}</p>}
              <div>
                <p className="mb-1 text-sm font-medium text-text">Parameters</p>
                <pre className="max-h-40 overflow-auto rounded-md bg-subtle p-3 text-xs">{JSON.stringify(data.params ?? {}, null, 2)}</pre>
              </div>
              <div>
                <p className="mb-1 text-sm font-medium text-text">Result summary (secrets redacted, size-limited)</p>
                <pre className="max-h-72 overflow-auto rounded-md bg-subtle p-3 text-xs">{data.result ? JSON.stringify(data.result, null, 2) : "No result reported yet."}</pre>
              </div>
              {cancellable && (
                <Can perm="servers:write">
                  <Button variant="danger" onClick={() => setConfirm(true)}>
                    <XCircle className="h-4 w-4" /> Cancel job
                  </Button>
                </Can>
              )}
            </>
          )}
        </div>
        <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="Cancel this job?" description="A running job may already have started on the server; its result will be ignored." confirmLabel="Cancel job" destructive onConfirm={() => void cancel()} />
      </SheetContent>
    </Sheet>
  );
}

export function JobsPanel({ serverId }: { serverId?: string }) {
  const [status, setStatus] = React.useState("");
  const [operation, setOperation] = React.useState("");
  const [detail, setDetail] = React.useState<string | null>(null);
  const [run, setRun] = React.useState(false);
  const [nonce, setNonce] = React.useState(0);
  const ops = useApi<Operation[]>("/jobs/operations");

  const columns: Column<JobRow>[] = [
    { id: "op", header: "Operation", sortValue: (r) => r.operation, cell: (r) => <code className="text-xs">{r.operation}</code> },
    { id: "status", header: "Status", sortValue: (r) => r.status, cell: (r) => <StatusPill status={r.status} /> },
    { id: "created", header: "Queued", sortValue: (r) => r.created_at, cell: (r) => <span className="tnum">{fmtDate(r.created_at)}</span> },
    { id: "dur", header: "Duration", hideOnMobile: true, cell: (r) => <span className="tnum">{duration(r)}</span> },
    { id: "attempts", header: "Attempts", hideOnMobile: true, defaultHidden: true, cell: (r) => r.attempts },
  ];
  const filters: FilterDef<JobRow>[] = [];
  const opFields: FieldDef[] = [
    { name: "operation", label: "Operation", type: "select", required: true, options: (ops.data ?? []).map((o) => ({ value: o.name, label: o.name + (o.destructive ? " (destructive)" : "") })), hint: "Only allow-listed operations can be queued." },
    { name: "params", label: "Parameters (JSON, optional)", type: "textarea", placeholder: "{}" },
  ];

  return (
    <>
      <ResourceTable<JobRow>
        key={`${nonce}${status}${operation}`}
        path="/jobs"
        query={{ server_id: serverId, status: status || undefined, operation: operation || undefined, page_size: 100 }}
        caption="Operations"
        columns={columns}
        filters={filters}
        searchText={(r) => `${r.operation} ${r.status}`}
        refreshMs={15000}
        emptyTitle="No operations yet"
        emptyDescription="Queued, running and finished agent operations appear here."
        toolbarExtra={
          <>
            <select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)} className="h-8 rounded-md border border-border-strong bg-surface px-2 text-sm">
              <option value="">All statuses</option>
              {STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
            </select>
            <select aria-label="Filter by operation" value={operation} onChange={(e) => setOperation(e.target.value)} className="h-8 rounded-md border border-border-strong bg-surface px-2 text-sm">
              <option value="">All operations</option>
              {(ops.data ?? []).map((o) => <option key={o.name} value={o.name}>{o.name}</option>)}
            </select>
            <Can perm="servers:write">
              {serverId && (
                <Button size="sm" variant="outline" onClick={() => setRun(true)}>
                  <Play className="h-4 w-4" /> Run operation
                </Button>
              )}
            </Can>
          </>
        }
        rowActions={(r) => (
          <Button size="sm" variant="ghost" onClick={() => setDetail(r.id)} aria-label={`Details for ${r.operation}`}>
            <Eye className="h-4 w-4" /> Details
          </Button>
        )}
      />
      <JobDetail id={detail} onClose={() => setDetail(null)} onChanged={() => setNonce((n) => n + 1)} />
      <FormDialog
        open={run}
        onOpenChange={setRun}
        title="Run operation"
        description="Queues an allow-listed operation for the agent. Parameters are validated by the server and again by the agent."
        fields={opFields}
        submitLabel="Queue"
        successMessage="Operation queued"
        onSubmit={async (v) => {
          let params: unknown = {};
          if (typeof v.params === "string" && v.params.trim()) {
            try {
              params = JSON.parse(v.params);
            } catch {
              throw new ApiError(422, "invalid_json", "Parameters must be valid JSON.", [{ field: "params", message: "Not valid JSON" }]);
            }
          }
          await post(`/servers/${serverId}/jobs`, { operation: v.operation, params });
          setNonce((n) => n + 1);
        }}
      />
    </>
  );
}
