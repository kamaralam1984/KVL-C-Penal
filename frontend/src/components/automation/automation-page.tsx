"use client";

import * as React from "react";
import { Play, RotateCw } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { ResourceTable } from "@/components/resource/resource-table";
import { Can, DetailList, RequirePerm, StatusPill, fmtDate } from "@/components/resource/helpers";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KpiCard } from "@/components/ui/kpi-card";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useApi } from "@/hooks/use-api";
import { useToast } from "@/components/ui/toast";
import { ApiError, post } from "@/lib/http";
import type { Column } from "@/components/ui/data-table";

interface Status { integration: { inbound: string; outbound: string; base_url_set: boolean; signature_window_seconds: number }; outbox: { pending: number; delivered: number; dead: number } }
interface Workflow { key: string; name: string; description: string; enabled: boolean; events: string[]; import_file: string; last_status: string | null; last_run_at: string | null; failed_runs: number }
interface Outbox { id: string; event_type: string; workflow: string; status: string; attempts: number; max_attempts: number; next_attempt_at: string; last_error: string | null; correlation_id: string; created_at: string; payload?: Record<string, unknown> }
interface Execution { id: string; workflow_key: string; correlation_id: string; status: string; error: string | null; escalated: boolean; started_at: string; finished_at: string | null }

function Conn({ label, value }: { label: string; value: string }) {
  return <div className="flex items-center justify-between gap-2 py-2.5 text-sm"><span className="text-muted">{label}</span><StatusPill status={value === "configured" ? "connected" : "not_configured"} /></div>;
}

export function AutomationPage() {
  const toast = useToast();
  const [tick, setTick] = React.useState(0);
  const bump = () => setTick((t) => t + 1);
  const st = useApi<Status>("/automation/status", { t: tick }, { refreshMs: 15000 });
  const wf = useApi<{ items: Workflow[] }>("/automation/workflows", { t: tick });
  const [sel, setSel] = React.useState<Outbox | null>(null);
  const [retry, setRetry] = React.useState<Outbox | null>(null);
  const configured = st.data?.integration.outbound === "configured";

  const evCols: Column<Outbox>[] = [
    { id: "t", header: "Event", sortValue: (r) => r.event_type, cell: (r) => <div><p className="font-mono text-xs text-text">{r.event_type}</p><p className="text-xs text-muted">{r.workflow.replace(/_/g, " ")}</p></div> },
    { id: "s", header: "Status", sortValue: (r) => r.status, cell: (r) => <StatusPill status={r.status === "pending" && r.attempts > 0 ? "warning" : r.status === "dead" ? "failed" : r.status === "delivered" ? "succeeded" : "pending"} /> },
    { id: "a", header: "Attempts", hideOnMobile: true, align: "right", cell: (r) => <span className="tnum">{r.attempts}/{r.max_attempts}</span> },
    { id: "c", header: "Created", hideOnMobile: true, sortValue: (r) => r.created_at, cell: (r) => fmtDate(r.created_at) },
  ];
  const exCols: Column<Execution>[] = [
    { id: "w", header: "Workflow", sortValue: (r) => r.workflow_key, cell: (r) => <div><p className="text-text">{r.workflow_key.replace(/_/g, " ")}</p><p className="font-mono text-[11px] text-faint">{r.correlation_id.slice(0, 12)}</p></div> },
    { id: "s", header: "Status", sortValue: (r) => r.status, cell: (r) => <StatusPill status={r.status} /> },
    { id: "e", header: "Error", hideOnMobile: true, cell: (r) => <span className="line-clamp-1 text-xs text-danger">{r.error ?? ""}</span> },
    { id: "t", header: "Started", hideOnMobile: true, sortValue: (r) => r.started_at, cell: (r) => fmtDate(r.started_at) },
  ];

  async function run(path: string, title: string) {
    try { const r = await post<Record<string, unknown>>(path); toast({ title, description: Object.entries(r).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(", "), tone: r.status === "not_configured" ? "warning" : "success" }); bump(); }
    catch (e) { toast({ title: "Failed", description: (e as ApiError).message, tone: "danger" }); }
  }

  return (
    <RequirePerm perm="automation:read">
      <PageHeader title="Automation" description="n8n workflows. The backend stays the source of truth; workflows act only through the signed API." actions={<Can perm="automation:manage"><Button size="sm" variant="outline" onClick={() => void run("/automation/scheduler/run", "Reminders emitted")}>Emit reminders</Button><Button size="sm" onClick={() => void run("/automation/dispatch", "Dispatch finished")}><Play className="h-3.5 w-3.5" />Dispatch now</Button></Can>} />
      {st.error ? <ErrorState onRetry={st.reload} /> : (
        <div className="mb-4 grid gap-4 lg:grid-cols-3">
          <Card><CardHeader title="Integration" /><CardBody className="divide-y divide-border">
            {st.loading || !st.data ? <Skeleton className="h-20 w-full" /> : <><Conn label="Inbound (n8n to KVL)" value={st.data.integration.inbound} /><Conn label="Outbound (KVL to n8n)" value={st.data.integration.outbound} /></>}
            {!configured && st.data && <p className="pt-3 text-xs text-muted">Not configured: set N8N_WEBHOOK_SECRET and N8N_BASE_URL on the backend. Events stay queued until then. See docs/runbooks/n8n.md; keep n8n on a private network.</p>}
          </CardBody></Card>
          <KpiCard label="Queued / retrying" value={String(st.data?.outbox.pending ?? 0)} hint="Waiting for delivery" />
          <KpiCard label="Dead-lettered" value={String(st.data?.outbox.dead ?? 0)} deltaGoodWhen="down" hint="Need review or retry" />
        </div>
      )}
      <Tabs defaultValue="workflows">
        <TabsList className="mb-4 overflow-x-auto"><TabsTrigger value="workflows">Workflow catalog</TabsTrigger><TabsTrigger value="executions">Executions</TabsTrigger><TabsTrigger value="outbox">Event outbox</TabsTrigger><TabsTrigger value="dead">Failures</TabsTrigger></TabsList>
        <TabsContent value="workflows">
          {wf.loading ? <Skeleton className="h-40 w-full" /> : wf.error ? <ErrorState onRetry={wf.reload} /> : (
            <div className="grid gap-4 md:grid-cols-2">{wf.data?.items.map((w) => (
              <Card key={w.key}><CardHeader title={w.name} description={w.description} actions={<StatusPill status={w.enabled ? "active" : "disabled"} />} /><CardBody>
                <DetailList items={[["Last run", w.last_status ? <span key="l" className="inline-flex items-center gap-2"><StatusPill status={w.last_status} />{fmtDate(w.last_run_at)}</span> : "Never"], ["Failed runs", w.failed_runs], ["Events", <span key="e" className="font-mono text-xs">{w.events.join(", ")}</span>], ["Import file", <span key="i" className="font-mono text-xs">{w.import_file}</span>]]} />
                <Can perm="automation:manage"><Button size="sm" variant="ghost" className="mt-2" onClick={async () => { await post(`/automation/workflows/${w.key}/enabled`, { enabled: !w.enabled }); bump(); }}>{w.enabled ? "Disable (stop queuing events)" : "Enable"}</Button></Can>
              </CardBody></Card>))}</div>
          )}
        </TabsContent>
        <TabsContent value="executions"><ResourceTable<Execution> key={`x${tick}`} path="/automation/executions" caption="Executions" columns={exCols} searchText={(r) => `${r.workflow_key} ${r.status} ${r.correlation_id}`} emptyTitle="No executions yet" emptyDescription="Executions are recorded when events are delivered to n8n." /></TabsContent>
        <TabsContent value="outbox"><ResourceTable<Outbox> key={`o${tick}`} path="/automation/outbox" caption="Events" columns={evCols} searchText={(r) => `${r.event_type} ${r.status} ${r.correlation_id}`} emptyTitle="No events yet" rowActions={(r) => <Button size="sm" variant="outline" onClick={() => setSel(r)}>Detail</Button>} /></TabsContent>
        <TabsContent value="dead"><ResourceTable<Outbox> key={`d${tick}`} path="/automation/outbox" query={{ status: "dead" }} caption="Dead-lettered events" columns={evCols} searchText={(r) => r.event_type} emptyTitle="No failures" emptyDescription="Events that exhaust their retries are listed here." rowActions={(r) => <><Button size="sm" variant="outline" onClick={() => setSel(r)}>Detail</Button><Can perm="automation:manage"><Button size="sm" onClick={() => setRetry(r)}><RotateCw className="h-3.5 w-3.5" />Retry</Button></Can></>} /></TabsContent>
      </Tabs>
      <Sheet open={!!sel} onOpenChange={(o) => !o && setSel(null)}><SheetContent title={sel?.event_type ?? ""} description="Event detail" className="w-[min(96vw,32rem)]">{sel && <EventDetail id={sel.id} />}</SheetContent></Sheet>
      <ConfirmDialog open={!!retry} onOpenChange={(o) => !o && setRetry(null)} title="Retry this event?" description="It is re-queued with the same event id and correlation id, so n8n can ignore it if it already ran. Retrying cannot repeat billing actions: the backend de-duplicates them." confirmLabel="Retry" onConfirm={async () => { await post(`/automation/outbox/${retry!.id}/retry`); bump(); }} />
    </RequirePerm>
  );
}

function EventDetail({ id }: { id: string }) {
  const d = useApi<Outbox>(`/automation/outbox/${id}`);
  if (d.loading) return <div className="p-5"><Skeleton className="h-32 w-full" /></div>;
  if (d.error || !d.data) return <ErrorState onRetry={d.reload} />;
  const e = d.data;
  return <div className="space-y-4 p-5"><DetailList items={[["Status", <StatusPill key="s" status={e.status} />], ["Workflow", e.workflow], ["Attempts", `${e.attempts}/${e.max_attempts}`], ["Next attempt", fmtDate(e.next_attempt_at)], ["Correlation id", <span key="c" className="font-mono text-xs">{e.correlation_id}</span>], ["Created", fmtDate(e.created_at)]]} />
    {e.last_error ? <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm break-words text-danger">{e.last_error}</p> : <EmptyState title="No error recorded" />}
    <div><h3 className="mb-1 text-sm font-semibold text-text">Payload (tenant-scoped, no secrets)</h3><pre className="max-h-64 overflow-auto rounded-md bg-subtle p-3 text-xs">{JSON.stringify(e.payload, null, 2)}</pre></div></div>;
}
