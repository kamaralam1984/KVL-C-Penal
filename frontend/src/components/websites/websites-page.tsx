"use client";

import * as React from "react";
import { Archive, ArrowLeft, ArrowRight, Check, Globe, Pause, Play, RefreshCw, RotateCcw, Rocket, ShieldCheck, Zap } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { ResourceTable } from "@/components/resource/resource-table";
import { Can, DetailList, RequirePerm, StatusPill, fmtDate } from "@/components/resource/helpers";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { ConfirmDialog, Dialog, DialogContent } from "@/components/ui/dialog";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import { Field, Input, Select } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/states";
import { useApi } from "@/hooks/use-api";
import { useToast } from "@/components/ui/toast";
import { ApiError, post } from "@/lib/http";
import { SiteResourceUsage } from "@/components/monitoring/site-resource-usage";
import { DnsInstructions, type Domain } from "@/components/domains/domains-page";
import type { Column, FilterDef } from "@/components/ui/data-table";

interface JobView { id: string; operation: string; status: string; result: Record<string, unknown> | null; error: string | null; finished_at: string | null }
interface Website {
  id: string; name: string; slug: string; plan_code: string | null; runtime_type: string; runtime_version: string | null; document_root: string; status: string;
  provision_state: string; provision_message: string | null; config_hash: string | null; created_at: string; archived_at: string | null; domains: string[];
  domain_details?: Domain[]; usage?: { domains: number; verified_domains: number; applications: number; disk_mb: number | null; disk_note: string | null };
  jobs?: Record<string, JobView | null>;
}
interface Activity { action: string; summary: string; created_at: string }
interface Quota { resource: string; limit: number; used: number }

const BUSY = ["queued", "activating", "reloading", "rolling_back", "removing"];
const RETRYABLE = ["failed", "pending_server", "not_configured", "awaiting_domain", "removed"];
const STEPS = [
  { id: "awaiting_domain", label: "Domain verified" },
  { id: "queued", label: "Validate config" },
  { id: "staged", label: "Staged" },
  { id: "installed", label: "Installed" },
  { id: "active", label: "Live" },
];
const ORDER: Record<string, number> = { pending_server: -1, awaiting_domain: 0, queued: 1, staged: 2, activating: 2, installed: 3, reloading: 3, rolled_back: 3, active: 4 };

function Progress({ state }: { state: string }) {
  const at = ORDER[state] ?? -1;
  const bad = ["failed", "not_configured", "pending_server"].includes(state);
  return (
    <ol className="flex flex-wrap gap-x-4 gap-y-2" aria-label="Provisioning progress">
      {STEPS.map((s, i) => {
        const done = i < at || (i === at && state === "active");
        const cur = i === at && !done;
        return (
          <li key={s.id} className={`flex items-center gap-1.5 text-xs ${done ? "text-success" : cur ? (bad ? "text-danger" : "text-primary") : "text-faint"}`} aria-current={cur ? "step" : undefined}>
            <span className={`grid h-5 w-5 place-items-center rounded-full border text-[10px] ${done ? "border-success bg-success/15" : cur ? "border-current" : "border-border"} ${cur && BUSY.includes(state) ? "icon-pulse" : ""}`}>{done ? <Check className="h-3 w-3" /> : i + 1}</span>
            {s.label}
          </li>
        );
      })}
    </ol>
  );
}

function QuotaMeter({ q }: { q: Quota }) {
  const pct = q.limit ? Math.min(100, (q.used / q.limit) * 100) : 100;
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs"><span className="text-muted capitalize">{q.resource.replace("_", " ")}</span><span className="tnum text-text">{q.used} / {q.limit}</span></div>
      <div role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label={`${q.resource} quota`} className="h-1.5 overflow-hidden rounded-full bg-subtle"><div className={`h-full rounded-full ${pct >= 90 ? "bg-danger" : pct >= 75 ? "bg-warning" : "bg-primary"}`} style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

function WebsiteDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const toast = useToast();
  const [busyState, setBusy] = React.useState<string | null>(null);
  const [poll, setPoll] = React.useState<number | undefined>(undefined);
  const w = useApi<Website>(`/websites/${id}`, undefined, { refreshMs: poll });
  const activity = useApi<Activity[]>(`/websites/${id}/activity`);
  const usage = useApi<{ quotas: Quota[] }>(`/websites/${id}/usage`);
  const [confirm, setConfirm] = React.useState<null | { kind: string; title: string; text: string; label: string }>(null);
  const site = w.data;
  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPoll(site && BUSY.includes(site.provision_state) ? 3000 : undefined);
  }, [site]);
  const prevState = React.useRef<string>("");
  React.useEffect(() => {
    if (site && prevState.current && prevState.current !== site.provision_state) {
      activity.reload();
      onChanged();
    }
    if (site) prevState.current = site.provision_state;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [site?.provision_state]);

  async function run(kind: string, body: unknown = {}) {
    setBusy(kind);
    try {
      await post(`/websites/${id}/${kind}`, body);
      toast({ title: "Request sent", description: "The agent will report back; progress updates here.", tone: "success" });
    } catch (e) {
      toast({ title: "Action failed", description: e instanceof ApiError ? e.message : "Network error", tone: "danger" });
    } finally {
      setBusy(null);
      w.reload();
      activity.reload();
      onChanged();
    }
  }

  if (!site) return <p className="p-5 text-sm text-muted">{w.error ? w.error.message : "Loading…"}</p>;
  const st = site.provision_state;
  const busy = BUSY.includes(st);
  return (
    <div className="space-y-5 p-5">
      <div className="flex flex-wrap items-center gap-2"><StatusPill status={site.status} /><StatusPill status={st} />{busy && <span className="text-xs text-muted" role="status">Working… checking every 3 s</span>}</div>
      <Progress state={st} />
      {site.provision_message && (
        <p role="status" className={`rounded-md px-3 py-2 text-sm ${["failed", "not_configured"].includes(st) ? "bg-danger/10 text-danger" : st === "pending_server" ? "bg-warning/10 text-warning" : "bg-subtle text-muted"}`}>{site.provision_message}</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Can perm="websites:write">
          {site.status === "active" && !busy && RETRYABLE.concat(["staged", "installed", "active", "rolled_back"]).includes(st) && (
            <Button size="sm" loading={busyState === "provision"} onClick={() => void run("provision")}>{RETRYABLE.includes(st) && st !== "awaiting_domain" ? <RefreshCw className="h-3.5 w-3.5" /> : <ShieldCheck className="h-3.5 w-3.5" />}{RETRYABLE.includes(st) && st !== "awaiting_domain" ? "Retry" : "Validate config"}</Button>
          )}
        </Can>
        <Can perm="websites:manage">
          {st === "staged" && <Button size="sm" loading={busyState === "activate"} onClick={() => setConfirm({ kind: "activate", title: "Install this configuration?", text: "The validated config replaces the site's live file after a backup. Nginx is not reloaded yet.", label: "Install" })}><Rocket className="h-3.5 w-3.5" />Activate</Button>}
          {(st === "installed" || st === "rolled_back") && <Button size="sm" loading={busyState === "reload"} onClick={() => setConfirm({ kind: "reload", title: "Reload Nginx?", text: "Nginx re-tests the configuration and reloads only if the test passes.", label: "Reload" })}><Zap className="h-3.5 w-3.5" />Reload Nginx</Button>}
          {["installed", "active", "rolled_back"].includes(st) && <Button size="sm" variant="outline" onClick={() => setConfirm({ kind: "rollback", title: "Roll back configuration?", text: "Restores the most recent backup of this site's Nginx config (the current one is saved first). Reload afterwards.", label: "Roll back" })}><RotateCcw className="h-3.5 w-3.5" />Rollback</Button>}
          {site.status === "active" && <Button size="sm" variant="outline" onClick={() => setConfirm({ kind: "suspend", title: "Suspend this website?", text: "A live Nginx config is removed (backup kept). The site goes offline after a reload.", label: "Suspend" })}><Pause className="h-3.5 w-3.5" />Suspend</Button>}
          {site.status === "suspended" && <Button size="sm" variant="outline" onClick={() => void run("reactivate")}><Play className="h-3.5 w-3.5" />Reactivate</Button>}
          {site.status !== "archived" && <Button size="sm" variant="danger" onClick={() => setConfirm({ kind: "archive", title: "Archive this website?", text: "Domains are unmapped and the website quota is released. This cannot be undone from the panel.", label: "Archive" })}><Archive className="h-3.5 w-3.5" />Archive</Button>}
        </Can>
      </div>

      <section aria-label="Overview"><DetailList items={[["Runtime", `${site.runtime_type}${site.runtime_version ? ` ${site.runtime_version}` : ""}`], ["Document root", <code key="r" className="text-xs break-all">{site.document_root}</code>], ["Plan", site.plan_code], ["Created", fmtDate(site.created_at)]]} /></section>

      <section aria-label="Usage" className="space-y-2">
        <h3 className="text-sm font-semibold text-text">Usage</h3>
        <DetailList items={[["Domains", `${site.usage?.verified_domains ?? 0} verified of ${site.usage?.domains ?? 0}`], ["Applications", site.usage?.applications ?? 0], ["Disk", site.usage?.disk_mb != null ? `${site.usage.disk_mb} MB` : "Not reported yet"]]} />
        {site.usage?.disk_mb == null && <p className="text-xs text-muted">Disk usage is collected by the monitoring agent and has not been reported for this site.</p>}
        <div className="space-y-2">{usage.data?.quotas.map((q) => <QuotaMeter key={q.resource} q={q} />)}</div>
      </section>

      <section aria-label="Resource usage" className="space-y-2">
        <h3 className="text-sm font-semibold text-text">RAM, CPU and data</h3>
        <SiteResourceUsage websiteId={id} diskQuotaMb={usage.data?.quotas.find((q) => q.resource === "disk_mb")?.limit} />
      </section>

      <section aria-label="Domains" className="space-y-3">
        <h3 className="text-sm font-semibold text-text">Domains</h3>
        {!site.domain_details?.length ? <p className="text-sm text-muted">No domains mapped. Add one on the Domains page and map it here.</p> : site.domain_details.map((d) => (
          <div key={d.id} className="space-y-2"><div className="flex items-center justify-between gap-2"><span className="font-medium break-all text-sm text-text">{d.hostname}</span><StatusPill status={d.verification_state} /></div>{d.verification_state !== "verified" && <DnsInstructions domain={d} onChanged={() => w.reload()} />}</div>
        ))}
      </section>

      {site.jobs && Object.values(site.jobs).some(Boolean) && (
        <section aria-label="Jobs" className="space-y-2">
          <h3 className="text-sm font-semibold text-text">Agent jobs</h3>
          <ul className="divide-y divide-border text-sm">{Object.values(site.jobs).filter((j): j is JobView => !!j).map((j) => (
            <li key={j.id} className="flex items-center justify-between gap-2 py-2"><span className="text-muted">{j.operation}</span><StatusPill status={j.status} /></li>
          ))}</ul>
        </section>
      )}

      <section aria-label="Activity" className="space-y-2">
        <h3 className="text-sm font-semibold text-text">Activity</h3>
        {!activity.data?.length ? <EmptyState title="No activity yet" /> : <ol className="space-y-3 border-l border-border pl-4">{activity.data.map((a, i) => (<li key={i} className="relative text-sm"><span className="absolute top-1.5 -left-[1.3rem] h-2 w-2 rounded-full bg-primary" aria-hidden /><p className="text-text">{a.summary}</p><p className="text-xs text-faint">{fmtDate(a.created_at)}</p></li>))}</ol>}
      </section>

      <ConfirmDialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)} title={confirm?.title ?? ""} description={confirm?.text ?? ""} confirmLabel={confirm?.label} destructive={confirm?.kind === "archive" || confirm?.kind === "suspend"} onConfirm={() => confirm && void run(confirm.kind)} />
    </div>
  );
}

function AddWizard({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (id: string) => void }) {
  const toast = useToast();
  const [step, setStep] = React.useState(0);
  const [name, setName] = React.useState("");
  const [runtime, setRuntime] = React.useState("static");
  const [version, setVersion] = React.useState("8.3");
  const [domain, setDomain] = React.useState("");
  const [err, setErr] = React.useState<ApiError>();
  const [pending, setPending] = React.useState(false);
  React.useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStep(0); setName(""); setRuntime("static"); setDomain(""); setErr(undefined);
    }
  }, [open]);
  const steps = ["Site", "Domain", "Review"];
  async function submit() {
    setPending(true);
    setErr(undefined);
    try {
      const w = await post<Website>("/websites", { name, runtime_type: runtime, ...(runtime === "php" ? { runtime_version: version } : {}), ...(domain ? { domain } : {}) });
      toast({ title: "Website created", tone: "success" });
      onOpenChange(false);
      onCreated(w.id);
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, "network", "Network error"));
    } finally {
      setPending(false);
    }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Add website" description={`Step ${step + 1} of 3 — ${steps[step]}`}>
        <ol className="mb-4 flex gap-2" aria-label="Steps">{steps.map((s, i) => <li key={s} aria-current={i === step ? "step" : undefined} className={`h-1.5 flex-1 rounded-full ${i <= step ? "bg-primary" : "bg-subtle"}`} title={s} />)}</ol>
        <div className="space-y-4">
          {step === 0 && (<>
            <Field label="Website name" required>{(p) => <Input {...p} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />}</Field>
            <Field label="Runtime" hint="PHP versions are checked against what the server has installed.">{(p) => <Select {...p} value={runtime} onChange={(e) => setRuntime(e.target.value)}><option value="static">Static files</option><option value="php">PHP (PHP-FPM)</option></Select>}</Field>
            {runtime === "php" && <Field label="PHP version">{(p) => <Select {...p} value={version} onChange={(e) => setVersion(e.target.value)}>{["8.4", "8.3", "8.2", "8.1", "8.0", "7.4"].map((v) => <option key={v}>{v}</option>)}</Select>}</Field>}
          </>)}
          {step === 1 && <Field label="Primary domain (optional)" hint="You can add it later. Ownership is verified with a DNS TXT record.">{(p) => <Input {...p} value={domain} placeholder="www.example.com" onChange={(e) => setDomain(e.target.value)} />}</Field>}
          {step === 2 && <DetailList items={[["Name", name], ["Runtime", runtime === "php" ? `PHP ${version}` : "Static"], ["Domain", domain || "None yet"], ["Server", "Assigned automatically"]]} />}
          {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err.code === "quota_exceeded" ? `Quota reached: ${err.message}` : err.message}</p>}
          <div className="flex justify-between gap-2 pt-1">
            <Button variant="outline" disabled={step === 0} onClick={() => setStep((s) => s - 1)}><ArrowLeft className="h-4 w-4" />Back</Button>
            {step < 2 ? <Button disabled={step === 0 && name.trim().length < 2} onClick={() => setStep((s) => s + 1)}>Next<ArrowRight className="h-4 w-4" /></Button> : <Button loading={pending} onClick={submit}>Create website</Button>}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

const columns: Column<Website>[] = [
  { id: "name", header: "Website", sortValue: (r) => r.name, cell: (r) => (<div className="min-w-0"><p className="font-medium text-text">{r.name}</p><p className="text-xs break-all text-muted">{r.domains[0] ?? "No domain"}{r.domains.length > 1 ? ` +${r.domains.length - 1}` : ""}</p></div>) },
  { id: "status", header: "Status", sortValue: (r) => r.status, cell: (r) => <StatusPill status={r.status} /> },
  { id: "prov", header: "Provisioning", sortValue: (r) => r.provision_state, cell: (r) => <StatusPill status={r.provision_state} /> },
  { id: "rt", header: "Runtime", hideOnMobile: true, cell: (r) => `${r.runtime_type}${r.runtime_version ? ` ${r.runtime_version}` : ""}` },
  { id: "created", header: "Created", hideOnMobile: true, sortValue: (r) => r.created_at, cell: (r) => fmtDate(r.created_at) },
];
const filters: FilterDef<Website>[] = [{ id: "state", label: "Provisioning", options: ["pending_server", "awaiting_domain", "queued", "staged", "installed", "active", "failed", "not_configured"], match: (r, v) => r.provision_state === v }];

export function WebsitesPage() {
  const [tick, setTick] = React.useState(0);
  const [adding, setAdding] = React.useState(false);
  const [sel, setSel] = React.useState<string | null>(null);
  const reloadList = React.useCallback(() => setTick((t) => t + 1), []);
  return (
    <RequirePerm perm="websites:read">
      <PageHeader title="Websites" description="Create websites, map verified domains and take them live through validated Nginx configuration." actions={<AnimatedIcon icon={Globe} idle="float" className="text-primary" size={26} />} />
      <ResourceTable<Website>
        key={tick}
        path="/websites"
        caption="Websites"
        columns={columns}
        filters={filters}
        searchText={(r) => `${r.name} ${r.domains.join(" ")}`}
        onCreate={() => setAdding(true)}
        createLabel="Add website"
        refreshMs={15000}
        emptyDescription="Add your first website. A server must be registered before it can go live."
        rowActions={(r) => <Button size="sm" variant="outline" onClick={() => setSel(r.id)}>Open</Button>}
      />
      <AddWizard open={adding} onOpenChange={setAdding} onCreated={(id) => { reloadList(); setSel(id); }} />
      <Sheet open={!!sel} onOpenChange={(o) => !o && setSel(null)}>
        <SheetContent title="Website" description="Status, provisioning and activity" className="w-[min(96vw,34rem)]">
          {sel && <WebsiteDetail key={sel} id={sel} onChanged={reloadList} />}
        </SheetContent>
      </Sheet>
    </RequirePerm>
  );
}
