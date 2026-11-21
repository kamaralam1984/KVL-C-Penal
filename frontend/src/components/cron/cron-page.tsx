"use client";

import * as React from "react";
import { History, Pause, Play, Trash2, Zap } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { RequirePerm, StatusPill, fmtDate } from "@/components/resource/helpers";
import { ResourceTable } from "@/components/resource/resource-table";
import { FormDialog } from "@/components/resource/form-dialog";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, Dialog, DialogContent } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/components/providers/auth-provider";
import { useApi } from "@/hooks/use-api";
import { ApiError, api, del, patch, post } from "@/lib/http";
import type { Column } from "@/components/ui/data-table";

interface CronJob {
  id: string; website_id: string; name: string; url: string; schedule: string; enabled: boolean;
  next_run_at: string | null; last_run_at: string | null; last_status: string | null; last_http_code: number | null;
  last_error: string | null; consecutive_failures: number; disabled_reason: string | null;
}
interface Run { id: string; trigger: string; status: string; http_code: number | null; duration_ms: number; error: string | null; started_at: string }
interface SiteLite { id: string; name: string }

const PRESETS = [
  { value: "*/15 * * * *", label: "Every 15 minutes" },
  { value: "*/30 * * * *", label: "Every 30 minutes" },
  { value: "0 * * * *", label: "Every hour" },
  { value: "0 */6 * * *", label: "Every 6 hours" },
  { value: "0 2 * * *", label: "Daily at 02:00 UTC" },
  { value: "0 3 * * 0", label: "Weekly (Sunday 03:00 UTC)" },
  { value: "custom", label: "Custom…" },
];
const human = (s: string) => PRESETS.find((p) => p.value === s)?.label ?? s;
const msg = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong");

function Runs({ job, onClose }: { job: CronJob | null; onClose: () => void }) {
  const runs = useApi<{ items: Run[] }>(job ? `/cron-jobs/${job.id}/runs` : null);
  return (
    <Dialog open={!!job} onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={`Run history · ${job?.name ?? ""}`} className="max-w-2xl">
        <ul className="divide-y divide-border text-sm">
          {runs.data?.items.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-3 py-2">
              <div className="min-w-0"><p className="text-text">{fmtDate(r.started_at)} · {r.trigger}</p>{r.error && <p className="truncate text-xs text-danger">{r.error}</p>}</div>
              <div className="flex shrink-0 items-center gap-2 text-xs text-muted tabular-nums">{r.http_code ?? "—"} · {r.duration_ms} ms<StatusPill status={r.status === "ok" ? "succeeded" : "failed"} /></div>
            </li>
          ))}
          {runs.data && runs.data.items.length === 0 && <li className="py-4 text-muted">No runs yet.</li>}
        </ul>
      </DialogContent>
    </Dialog>
  );
}

export function CronPage() {
  const toast = useToast();
  const { can } = useAuth();
  const sites = useApi<{ items: SiteLite[] }>("/websites", { limit: 100 });
  const [creating, setCreating] = React.useState(false);
  const [ver, setVer] = React.useState(0);
  const [history, setHistory] = React.useState<CronJob | null>(null);
  const [removing, setRemoving] = React.useState<{ j: CronJob; reload: () => void } | null>(null);
  const write = can("websites:write");
  const siteName = (id: string) => sites.data?.items.find((s) => s.id === id)?.name ?? "—";

  const act = async (fn: () => Promise<unknown>, ok: string, reload: () => void) => {
    try { await fn(); toast({ title: ok, tone: "success" }); } catch (e) { toast({ title: "Action failed", description: msg(e), tone: "danger" }); } finally { reload(); }
  };

  const cols: Column<CronJob>[] = [
    { id: "name", header: "Job", sortValue: (r) => r.name, cell: (r) => (<div className="min-w-0"><p className="font-medium text-text">{r.name}</p><p className="truncate text-xs text-muted" title={r.url}>{r.url}</p></div>) },
    { id: "site", header: "Website", hideOnMobile: true, cell: (r) => siteName(r.website_id) },
    { id: "schedule", header: "Schedule", cell: (r) => <span title={r.schedule}>{human(r.schedule)}</span> },
    { id: "status", header: "Status", cell: (r) => (<div><StatusPill status={!r.enabled ? (r.disabled_reason ? "suspended" : "disabled") : r.last_status === null ? "pending" : r.last_status === "ok" ? "active" : "failed"} />{r.disabled_reason && <p className="mt-1 text-xs text-danger">{r.disabled_reason}</p>}</div>) },
    { id: "last", header: "Last run", hideOnMobile: true, sortValue: (r) => r.last_run_at ?? "", cell: (r) => (r.last_run_at ? <span title={r.last_error ?? undefined}>{fmtDate(r.last_run_at)}{r.last_http_code ? ` · ${r.last_http_code}` : ""}</span> : "—") },
    { id: "next", header: "Next run", hideOnMobile: true, cell: (r) => (r.next_run_at ? fmtDate(r.next_run_at) : "—") },
  ];

  return (
    <RequirePerm perm="websites:read">
      <PageHeader title="Cron jobs" description="Call a URL on your own website on a schedule, e.g. WordPress wp-cron.php or a Laravel /schedule endpoint. Up to 4 runs per hour per job." />
      <ResourceTable<CronJob>
        key={ver}
        path="/cron-jobs"
        caption="Cron jobs"
        columns={cols}
        searchText={(r) => `${r.name} ${r.url}`}
        onCreate={() => setCreating(true)}
        createLabel="New cron job"
        canCreate={write}
        emptyTitle="No cron jobs yet"
        emptyDescription="Schedule a request to a URL on one of your verified domains."
        rowActions={(r, reload) => (
          <div className="flex justify-end gap-1">
            <Button size="sm" variant="ghost" onClick={() => setHistory(r)}><History className="h-4 w-4" /><span className="sr-only">History</span></Button>
            {write && (
              <>
                <Button size="sm" variant="ghost" onClick={() => void act(async () => { const x = await post<{ run: Run }>(`/cron-jobs/${r.id}/run`); if (x.run.status !== "ok") throw new Error(x.run.error ?? x.run.status); }, `${r.name} ran successfully`, reload)}><Zap className="h-4 w-4" /><span className="sr-only">Run now</span></Button>
                <Button size="sm" variant="ghost" onClick={() => void act(() => patch(`/cron-jobs/${r.id}`, { enabled: !r.enabled }), r.enabled ? "Paused" : "Resumed", reload)}>{r.enabled ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}<span className="sr-only">{r.enabled ? "Pause" : "Resume"}</span></Button>
                <Button size="sm" variant="ghost" onClick={() => setRemoving({ j: r, reload })}><Trash2 className="h-4 w-4" /><span className="sr-only">Delete</span></Button>
              </>
            )}
          </div>
        )}
      />
      <FormDialog
        open={creating}
        onOpenChange={setCreating}
        title="New cron job"
        description="The URL must be https and use a verified domain attached to the chosen website."
        fields={[
          { name: "website_id", label: "Website", type: "select", required: true, options: (sites.data?.items ?? []).map((s) => ({ value: s.id, label: s.name })) },
          { name: "name", label: "Name", required: true, placeholder: "WordPress cron" },
          { name: "url", label: "URL", required: true, placeholder: "https://example.com/wp-cron.php?doing_wp_cron" },
          { name: "preset", label: "Schedule", type: "select", required: true, defaultValue: "*/15 * * * *", options: PRESETS },
          { name: "schedule", label: "Cron expression (UTC)", placeholder: "minute hour day month weekday", hint: "Five fields, e.g. 0 */2 * * *. At most 4 runs per hour.", showIf: (v) => v.preset === "custom" },
        ]}
        submitLabel="Create"
        successMessage="Cron job created"
        onSubmit={async (v) => {
          const schedule = v.preset === "custom" ? String(v.schedule ?? "") : String(v.preset);
          await api("/cron-jobs", { method: "POST", body: { website_id: v.website_id, name: v.name, url: v.url, schedule } });
          setVer((x) => x + 1);
        }}
      />
      <Runs job={history} onClose={() => setHistory(null)} />
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} destructive title="Delete cron job?" description={`“${removing?.j.name}” and its run history will be removed.`} confirmLabel="Delete" onConfirm={() => removing && void act(() => del(`/cron-jobs/${removing.j.id}`), "Cron job deleted", removing.reload)} />
    </RequirePerm>
  );
}
