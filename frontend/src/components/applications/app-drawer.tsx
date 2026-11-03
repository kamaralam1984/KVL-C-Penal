"use client";

import * as React from "react";
import { ChevronLeft, ChevronRight, Eye, EyeOff, Play, RefreshCw, RotateCcw, Rocket, Square, Trash2 } from "lucide-react";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger, Segmented } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState, EmptyState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { Can, DetailList, StatusPill, fmtDate } from "@/components/resource/helpers";
import { useApi } from "@/hooks/use-api";
import { ApiError, del, post, put } from "@/lib/http";
import type { AppRow, Deployment, QuotaRow } from "./types";
import { RUNTIME_LABEL } from "./types";

const msg = (e: unknown) => (e instanceof ApiError ? e.message : "Network error");

function useAct(reload: () => void) {
  const toast = useToast();
  const [busy, setBusy] = React.useState(false);
  const run = async <T,>(fn: () => Promise<T>, ok: string): Promise<T | undefined> => {
    setBusy(true);
    try {
      const r = await fn();
      toast({ title: ok, tone: "success" });
      reload();
      return r;
    } catch (e) {
      toast({ title: "Action failed", description: msg(e), tone: "danger" });
    } finally {
      setBusy(false);
    }
  };
  return { busy, run };
}

export function Meter({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const tone = pct >= 90 ? "bg-danger" : pct >= 75 ? "bg-warning" : "bg-primary";
  return (
    <div>
      <div className="mb-1 flex justify-between text-sm">
        <span className="text-text">{label}</span>
        <span className="tnum text-muted">
          {used} / {limit}
        </span>
      </div>
      <div role="progressbar" aria-label={label} aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} className="h-2 overflow-hidden rounded-full bg-subtle">
        <div className={`h-full rounded-full transition-all ${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function AppDrawer({ appId, onClose }: { appId: string | null; onClose: () => void }) {
  const busyStates = ["deploying"];
  const [poll, setPoll] = React.useState(false);
  const app = useApi<AppRow>(appId ? `/apps/${appId}` : null, undefined, { refreshMs: poll ? 4000 : undefined });
  const a = app.data;
  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPoll(!!a && (busyStates.includes(a.provision_state) || a.last_job?.status === "queued" || a.last_job?.status === "running"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a]);

  return (
    <Sheet open={!!appId} onOpenChange={(o) => !o && onClose()}>
      <SheetContent title={a?.name ?? "Application"} description={a ? `${RUNTIME_LABEL[a.runtime]} ${a.runtime_version}` : "Loading…"} className="w-[min(96vw,44rem)]">
        <div className="p-4 sm:p-5">
          {app.loading && <Skeleton className="h-40 w-full" />}
          {app.error && <ErrorState onRetry={app.reload} />}
          {a && (
            <Tabs defaultValue="overview">
              <div className="-mx-1 overflow-x-auto">
                <TabsList className="min-w-max">
                  <TabsTrigger value="overview">Overview</TabsTrigger>
                  <TabsTrigger value="deployments">Deployments</TabsTrigger>
                  <TabsTrigger value="env">Environment</TabsTrigger>
                  {a.controllable && <TabsTrigger value="process">Process</TabsTrigger>}
                  {a.controllable && <TabsTrigger value="logs">Logs</TabsTrigger>}
                </TabsList>
              </div>
              <TabsContent value="overview" className="pt-4"><Overview a={a} reload={app.reload} /></TabsContent>
              <TabsContent value="deployments" className="pt-4"><Deployments a={a} reload={app.reload} /></TabsContent>
              <TabsContent value="env" className="pt-4"><EnvEditor a={a} /></TabsContent>
              {a.controllable && <TabsContent value="process" className="pt-4"><ProcessTab a={a} reload={app.reload} /></TabsContent>}
              {a.controllable && <TabsContent value="logs" className="pt-4"><LogsTab a={a} /></TabsContent>}
            </Tabs>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

// ---------------------------------------------------------------- overview
function Overview({ a, reload }: { a: AppRow; reload: () => void }) {
  const usage = useApi<{ usage: Record<string, number | null> | null; note: string | null; quotas: QuotaRow[] }>(`/apps/${a.id}/usage`);
  const { busy, run } = useAct(() => {
    reload();
    setTimeout(usage.reload, 3000);
  });
  const u = usage.data?.usage;
  const num = (v: number | null | undefined, unit: string) => (v == null ? "—" : `${v} ${unit}`);
  return (
    <div className="space-y-4">
      {a.provision_message && (
        <p role="status" className={`rounded-md px-3 py-2 text-sm ${["failed", "not_configured", "provisioning_blocked"].includes(a.provision_state) ? "bg-warning/14 text-warning" : "bg-subtle text-muted"}`}>
          {a.provision_message}
        </p>
      )}
      <Card>
        <CardBody className="pt-4">
          <DetailList
            items={[
              ["Process status", <StatusPill key="s" status={a.status} />],
              ["Deployment state", <StatusPill key="d" status={a.provision_state} />],
              ["Live release", a.current_release ?? "—"],
              ["Previous release", a.previous_release ?? "—"],
              ["Repository", a.repo_url ?? "—"],
              ["Branch", a.branch],
              ["Entrypoint", a.entrypoint ?? "—"],
              ["Health check", a.health_path],
              ["Port", a.port ?? "—"],
              ["Created", fmtDate(a.created_at)],
            ]}
          />
        </CardBody>
      </Card>
      <Card>
        <CardHeader
          title="Resource usage"
          actions={
            <Button size="sm" variant="outline" loading={busy} disabled={!a.current_release} onClick={() => run(() => post(`/apps/${a.id}/usage/refresh`), "Usage refresh requested")}>
              <RefreshCw className="h-3.5 w-3.5" /> Refresh
            </Button>
          }
        />
        <CardBody>
          {usage.loading ? <Skeleton className="h-16 w-full" /> : u ? (
            <DetailList items={[["Disk", num(u.disk_mb, "MB")], ["Memory", num(u.memory_mb, "MB")], ["CPU", num(u.cpu_percent, "%")], ["Restarts", u.restarts ?? "—"], ["Uptime", num(u.uptime_s, "s")], ["Releases on disk", u.releases ?? "—"]]} />
          ) : (
            <p className="text-sm text-muted">{usage.data?.note ?? "Usage has not been reported yet."}</p>
          )}
          <div className="mt-4 space-y-3">{usage.data?.quotas.map((q) => <Meter key={q.resource} label={`Quota: ${q.resource.replace("_", " ")}`} used={q.used} limit={q.limit} />)}</div>
        </CardBody>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- deployments
function Deployments({ a, reload }: { a: AppRow; reload: () => void }) {
  const deps = useApi<{ total: number; items: Deployment[] }>(`/apps/${a.id}/deployments`, { limit: 30 }, { refreshMs: a.provision_state === "deploying" ? 4000 : undefined });
  const { busy, run } = useAct(() => {
    reload();
    deps.reload();
  });
  const [rb, setRb] = React.useState(false);
  const deploying = a.provision_state === "deploying";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Can perm="apps:write">
          <Button loading={busy || deploying} onClick={() => run(() => post(`/apps/${a.id}/deploy`, {}), "Deployment queued")}>
            <Rocket className="h-4 w-4" /> {deploying ? "Deploying…" : "Deploy now"}
          </Button>
        </Can>
        <Can perm="apps:manage">
          <Button variant="outline" disabled={!a.previous_release || deploying} onClick={() => setRb(true)}>
            <RotateCcw className="h-4 w-4" /> Roll back
          </Button>
        </Can>
      </div>
      <p className="text-xs text-muted">A new release is built, health-checked, then switched in atomically. A failed release never replaces the live one.</p>
      {deps.loading ? <Skeleton className="h-24 w-full" /> : deps.error ? <ErrorState onRetry={deps.reload} /> : !deps.data?.items.length ? (
        <EmptyState title="No deployments yet" description="Deploy to build the first release." />
      ) : (
        <ol className="relative space-y-3 border-l border-border pl-4">
          {deps.data.items.map((d) => (
            <li key={d.id} className="relative">
              <span className={`absolute top-1.5 -left-[21px] h-2.5 w-2.5 rounded-full ${d.status === "succeeded" ? "bg-success" : d.status === "queued" ? "bg-info" : "bg-danger"}`} aria-hidden />
              <div className="flex flex-wrap items-center gap-2">
                <span className="tnum text-sm font-medium text-text">{d.release_id}</span>
                <StatusPill status={d.status} />
                {d.kind === "rollback" && <span className="text-xs text-muted">rollback</span>}
                {d.is_live && <span className="rounded-full bg-primary-soft px-2 py-0.5 text-[10px] font-semibold text-primary uppercase">live</span>}
              </div>
              <p className="text-xs text-muted">
                {fmtDate(d.created_at)}
                {d.branch ? ` · ${d.branch}` : ""}
                {d.commit ? ` · ${d.commit.slice(0, 8)}` : ""}
              </p>
              {d.error && <p className="mt-1 rounded-md bg-danger/10 px-2 py-1.5 text-xs break-words text-danger">{d.error}</p>}
            </li>
          ))}
        </ol>
      )}
      <ConfirmDialog open={rb} onOpenChange={setRb} title="Roll back to the previous release?" description={`Traffic switches to ${a.previous_release}. The current release stays on disk.`} confirmLabel="Roll back" destructive onConfirm={() => run(() => post(`/apps/${a.id}/rollback`, {}), "Rollback queued")} />
    </div>
  );
}

// ---------------------------------------------------------------- env
function EnvEditor({ a }: { a: AppRow }) {
  const env = useApi<{ name: string; value: string; updated_at: string }[]>(`/apps/${a.id}/env`);
  const { busy, run } = useAct(() => env.reload());
  const [name, setName] = React.useState("");
  const [value, setValue] = React.useState("");
  const [revealed, setRevealed] = React.useState<Record<string, string>>({});
  const [delName, setDelName] = React.useState<string | null>(null);
  const timers = React.useRef<number[]>([]);
  React.useEffect(() => () => timers.current.forEach(clearTimeout), []);

  async function reveal(n: string) {
    const r = await run(() => post<{ value: string }>(`/apps/${a.id}/env/${n}/reveal`), "Value revealed (audited)");
    if (r) {
      setRevealed((s) => ({ ...s, [n]: r.value }));
      timers.current.push(window.setTimeout(() => setRevealed((s) => { const c = { ...s }; delete c[n]; return c; }), 15000));
    }
  }
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">Values are encrypted at rest and masked. Changes apply on the next deploy. Revealing a value is recorded in the audit log and hides again after 15 seconds.</p>
      <Can perm="apps:write">
        <Card className="p-4">
          <form
            className="space-y-3"
            onSubmit={async (e) => {
              e.preventDefault();
              const r = await run(() => put(`/apps/${a.id}/env`, { name: name.trim(), value }), "Variable saved");
              if (r !== undefined || true) { /* keep fields on failure */ }
              setName("");
              setValue("");
            }}
          >
            <Field label="Name" hint="UPPER_SNAKE_CASE. Reserved names (PATH, NODE_OPTIONS, LD_*, GIT_* …) are refused.">
              {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value.toUpperCase())} placeholder="API_TOKEN" maxLength={63} />}
            </Field>
            <Field label="Value">
              {(p) => <Input {...p} type="password" autoComplete="new-password" value={value} onChange={(e) => setValue(e.target.value)} />}
            </Field>
            <Button type="submit" size="sm" loading={busy} disabled={!/^[A-Z][A-Z0-9_]*$/.test(name)}>
              Save variable
            </Button>
          </form>
        </Card>
      </Can>
      {env.loading ? <Skeleton className="h-16 w-full" /> : env.error ? <ErrorState onRetry={env.reload} /> : !env.data?.length ? (
        <EmptyState title="No environment variables" />
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {env.data.map((v) => (
            <li key={v.name} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
              <div className="min-w-0">
                <p className="text-sm font-medium break-all text-text">{v.name}</p>
                <code className="text-xs break-all text-muted">{revealed[v.name] ?? v.value}</code>
              </div>
              <div className="flex gap-1">
                <Can perm="apps:manage">
                  {revealed[v.name] ? (
                    <Button size="sm" variant="ghost" onClick={() => setRevealed((s) => { const c = { ...s }; delete c[v.name]; return c; })}><EyeOff className="h-4 w-4" /> Hide</Button>
                  ) : (
                    <Button size="sm" variant="ghost" onClick={() => reveal(v.name)}><Eye className="h-4 w-4" /> Reveal</Button>
                  )}
                </Can>
                <Can perm="apps:write">
                  <Button size="sm" variant="ghost" aria-label={`Delete ${v.name}`} onClick={() => setDelName(v.name)}><Trash2 className="h-4 w-4" /></Button>
                </Can>
              </div>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog open={!!delName} onOpenChange={(o) => !o && setDelName(null)} title={`Delete ${delName}?`} description="The variable is removed from the next deployment." confirmLabel="Delete" destructive onConfirm={() => run(() => del(`/apps/${a.id}/env/${delName}`), "Variable deleted")} />
    </div>
  );
}

// ---------------------------------------------------------------- process
function ProcessTab({ a, reload }: { a: AppRow; reload: () => void }) {
  const { busy, run } = useAct(reload);
  const [confirm, setConfirm] = React.useState<"stop" | "restart" | null>(null);
  const send = (action: string) => run(() => post(`/apps/${a.id}/process`, { action }), `Process ${action} requested`);
  const noRelease = !a.current_release;
  return (
    <div className="space-y-4">
      <Card className="p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm text-muted">Process status</p>
            <StatusPill status={a.status} />
          </div>
          <p className="text-xs text-muted">{a.last_job ? `Last action: ${a.last_job.operation.replace("apps.", "")} (${a.last_job.status})` : "No actions yet"}</p>
        </div>
      </Card>
      {noRelease && <p className="text-sm text-muted">Deploy the application before controlling its process.</p>}
      <Can perm="apps:manage" fallback={<p className="text-sm text-muted">Your role cannot control processes.</p>}>
        <div className="flex flex-wrap gap-2">
          <Button disabled={noRelease} loading={busy} onClick={() => send("start")}><Play className="h-4 w-4" /> Start</Button>
          <Button variant="outline" disabled={noRelease} onClick={() => setConfirm("restart")}><RotateCcw className="h-4 w-4" /> Restart</Button>
          <Button variant="outline" disabled={noRelease} onClick={() => setConfirm("stop")}><Square className="h-4 w-4" /> Stop</Button>
          <Button variant="ghost" disabled={noRelease} onClick={() => send("status")}><RefreshCw className="h-4 w-4" /> Check status</Button>
        </div>
      </Can>
      <ConfirmDialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)} title={`${confirm === "stop" ? "Stop" : "Restart"} ${a.name}?`} description={confirm === "stop" ? "The application will stop serving requests until it is started again." : "The application restarts and briefly drops in-flight requests."} confirmLabel={confirm === "stop" ? "Stop" : "Restart"} destructive={confirm === "stop"} onConfirm={() => confirm && send(confirm)} />
    </div>
  );
}

// ---------------------------------------------------------------- logs
interface LogResp { status: string; lines: string[]; page?: number; has_more?: boolean; total_lines?: number; message?: string }

function LogsTab({ a }: { a: AppRow }) {
  const [stream, setStream] = React.useState<"out" | "err">("out");
  const [page, setPage] = React.useState(1);
  const [jobId, setJobId] = React.useState<string | null>(null);
  const [done, setDone] = React.useState(false);
  const [reqErr, setReqErr] = React.useState<string>();
  const logs = useApi<LogResp>(jobId ? `/apps/${a.id}/logs/${jobId}` : null, undefined, { refreshMs: jobId && !done ? 2000 : undefined });
  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDone(!!logs.data && !["queued", "running"].includes(logs.data.status));
  }, [logs.data]);

  async function load(s: "out" | "err", p: number) {
    setStream(s);
    setPage(p);
    setDone(false);
    setReqErr(undefined);
    try {
      const r = await post<{ job_id: string }>(`/apps/${a.id}/logs`, { stream: s, page: p, page_size: 100 });
      setJobId(r.job_id);
    } catch (e) {
      setReqErr(msg(e));
    }
  }
  const waiting = jobId && !done;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Segmented label="Log stream" value={stream} onChange={(v) => load(v, 1)} options={[{ value: "out", label: "Output" }, { value: "err", label: "Errors" }]} />
        <Button size="sm" variant="outline" loading={!!waiting} onClick={() => load(stream, page)}><RefreshCw className="h-3.5 w-3.5" /> {jobId ? "Reload" : "Load logs"}</Button>
      </div>
      <p className="text-xs text-muted">Newest lines first by page. Secrets and tokens are redacted before display.</p>
      {reqErr && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{reqErr}</p>}
      {waiting && <p className="text-sm text-muted" role="status">Waiting for the server agent to return the log page…</p>}
      {logs.data?.status === "failed" && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{logs.data.message}</p>}
      {logs.data?.status === "succeeded" && (
        logs.data.lines.length ? (
          <pre className="max-h-96 overflow-auto rounded-md border border-border bg-subtle p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-all text-text">{logs.data.lines.join("\n")}</pre>
        ) : <EmptyState title="No log lines on this page" />
      )}
      {logs.data?.status === "succeeded" && (
        <div className="flex items-center justify-between">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => load(stream, page - 1)}><ChevronLeft className="h-4 w-4" /> Newer</Button>
          <span className="tnum text-xs text-muted">Page {page}{logs.data.total_lines != null ? ` · ${logs.data.total_lines} lines` : ""}</span>
          <Button size="sm" variant="outline" disabled={!logs.data.has_more} onClick={() => load(stream, page + 1)}>Older <ChevronRight className="h-4 w-4" /></Button>
        </div>
      )}
    </div>
  );
}
