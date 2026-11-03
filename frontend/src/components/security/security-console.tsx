"use client";

import * as React from "react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { KpiCard } from "@/components/ui/kpi-card";
import { Button } from "@/components/ui/button";
import { Badge, type Tone } from "@/components/ui/badge";
import { Select } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConfirmDialog } from "@/components/ui/dialog";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { PageHeader } from "@/components/shell/page-header";
import { FormDialog } from "@/components/resource/form-dialog";
import { LiveBadge, RequirePerm, StatusPill, fmtDate } from "@/components/resource/helpers";
import { useToast } from "@/components/ui/toast";
import { useApi } from "@/hooks/use-api";
import { del, post, ApiError } from "@/lib/http";
import { ApiKeysPanel } from "./api-keys-panel";
import { WebhooksPanel } from "./webhooks-panel";
import { AuditTable } from "./audit-table";

interface Ev { id: string; created_at: string; kind: string; severity: string; summary: string; status: string; ip: string | null; routed: boolean; acknowledged_at: string | null }
interface Overview { open_events: Record<string, number>; active_sessions: number; active_api_keys: number; dead_webhook_deliveries: number; alert_routes: number }
interface Route { id: string; name: string; min_severity: string; kinds: string[]; channel: string; target: string | null; enabled: boolean }
interface Sess { id: string; email: string; ip: string | null; user_agent: string | null; created_at: string; last_used_at: string | null }
const SEV: Record<string, Tone> = { low: "neutral", medium: "info", high: "warning", critical: "danger" };

function Events() {
  const toast = useToast();
  const [status, setStatus] = React.useState("open");
  const [sev, setSev] = React.useState("");
  const ev = useApi<{ total: number; items: Ev[] }>("/security/events", { status, severity: sev, limit: 50 }, { refreshMs: 30000 });
  async function act(id: string, what: "acknowledge" | "resolve") {
    try { await post(`/security/events/${id}/${what}`); toast({ title: what === "resolve" ? "Resolved" : "Acknowledged", tone: "success" }); ev.reload(); } catch (e) { toast({ title: e instanceof ApiError ? e.message : "Failed", tone: "danger" }); }
  }
  return (
    <Card>
      <CardHeader title="Security events and incidents" description="Failed-login surges, refresh-token reuse and blocked privilege escalation." actions={<><LiveBadge />
        <Button size="sm" variant="outline" onClick={async () => { const r = await post<Record<string, number>>("/security/scan"); toast({ title: `Scan finished: ${Object.values(r).reduce((a, b) => a + b, 0)} new`, tone: "info" }); ev.reload(); }}>Run scan</Button></>} />
      <CardBody className="space-y-3">
        <div className="flex gap-2">
          <Select aria-label="Status" className="w-40" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option><option>open</option><option>acknowledged</option><option>resolved</option></Select>
          <Select aria-label="Severity" className="w-40" value={sev} onChange={(e) => setSev(e.target.value)}><option value="">Any severity</option>{["low", "medium", "high", "critical"].map((s) => <option key={s}>{s}</option>)}</Select>
        </div>
        {ev.error && <ErrorState onRetry={ev.reload} />}
        {ev.data?.items.length === 0 && <EmptyState title="No events" description="Nothing matches. Detection runs when the scan is triggered or on the periodic task." />}
        {ev.data?.items.map((e) => (
          <div key={e.id} className="rounded-lg border border-border p-3 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2"><Badge tone={SEV[e.severity]} dot>{e.severity}</Badge><span className="font-medium text-text">{e.kind.replace(/_/g, " ")}</span><StatusPill status={e.status} />{!e.routed && <Badge>not routed</Badge>}</div>
              <div className="flex gap-2">{e.status === "open" && <Button size="sm" variant="outline" onClick={() => act(e.id, "acknowledge")}>Acknowledge</Button>}{e.status !== "resolved" && <Button size="sm" onClick={() => act(e.id, "resolve")}>Resolve</Button>}</div>
            </div>
            <p className="mt-1 text-muted">{e.summary}</p>
            <p className="text-xs text-faint">{fmtDate(e.created_at)}{e.ip ? ` · ${e.ip}` : ""}</p>
          </div>
        ))}
      </CardBody>
    </Card>
  );
}

function Routes() {
  const routes = useApi<Route[]>("/security/alert-routes");
  const [adding, setAdding] = React.useState(false);
  const [rm, setRm] = React.useState<Route | null>(null);
  return (
    <Card>
      <CardHeader title="Alert routing" description="Where incidents are sent. With no routes, events are recorded but nobody is notified." actions={<Button size="sm" onClick={() => setAdding(true)}>Add route</Button>} />
      <CardBody className="space-y-2">
        {routes.data?.length === 0 && <p className="text-sm text-muted">No alert routes configured.</p>}
        {routes.data?.map((r) => (
          <div key={r.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm">
            <span><span className="font-medium text-text">{r.name}</span> · {r.channel}{r.target ? ` → ${r.target}` : " → platform webhooks"} · ≥ {r.min_severity}{r.kinds.length ? ` · ${r.kinds.join(", ")}` : ""}</span>
            <Button size="sm" variant="outline" onClick={() => setRm(r)}>Remove</Button>
          </div>
        ))}
      </CardBody>
      <FormDialog open={adding} onOpenChange={setAdding} title="Add alert route" fields={[
        { name: "name", label: "Name", required: true },
        { name: "channel", label: "Channel", type: "select", required: true, defaultValue: "email", options: [{ value: "email", label: "Email" }, { value: "webhook", label: "Platform webhooks (security.alert)" }] },
        { name: "target", label: "Email address", type: "email", showIf: (v) => v.channel === "email" },
        { name: "min_severity", label: "Minimum severity", type: "select", required: true, defaultValue: "high", options: ["low", "medium", "high", "critical"].map((s) => ({ value: s, label: s })) },
      ]} onSubmit={async (v) => { await post("/security/alert-routes", v); routes.reload(); }} />
      <ConfirmDialog open={!!rm} onOpenChange={(o) => !o && setRm(null)} title="Remove alert route?" description="Matching incidents will no longer be sent there." confirmLabel="Remove" destructive onConfirm={async () => { if (rm) { await del(`/security/alert-routes/${rm.id}`); routes.reload(); } }} />
    </Card>
  );
}

function Sessions() {
  const s = useApi<{ active: number; items: Sess[] }>("/security/sessions");
  const toast = useToast();
  const [rv, setRv] = React.useState<Sess | null>(null);
  return (
    <Card>
      <CardHeader title="Active sessions" description={`${s.data?.active ?? 0} signed-in sessions across the platform`} actions={<LiveBadge />} />
      <CardBody className="space-y-2">
        {s.error && <ErrorState onRetry={s.reload} />}
        {s.data?.items.map((x) => (
          <div key={x.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm">
            <span><span className="font-medium text-text">{x.email}</span> · {x.ip ?? "unknown IP"} · <span className="text-muted">{(x.user_agent ?? "").slice(0, 50)}</span> · last used {fmtDate(x.last_used_at ?? x.created_at)}</span>
            <Button size="sm" variant="outline" onClick={() => setRv(x)}>Revoke</Button>
          </div>
        ))}
      </CardBody>
      <ConfirmDialog open={!!rv} onOpenChange={(o) => !o && setRv(null)} title="Revoke this session?" description={`${rv?.email} will be signed out on that device.`} confirmLabel="Revoke" destructive
        onConfirm={async () => { if (rv) { await post(`/security/sessions/${rv.id}/revoke`); toast({ title: "Session revoked", tone: "success" }); s.reload(); } }} />
    </Card>
  );
}

function Abuse() {
  const a = useApi<{ login: Record<string, number>; otp: Record<string, number>; rate_limits: { name: string; limit: number; window_seconds: number }[]; incident_detection: Record<string, number>; attachments: Record<string, number>; webhooks: Record<string, number> }>("/security/abuse-settings");
  return (
    <Card>
      <CardHeader title="Rate-limit and abuse controls" description="Read-only. These are set in code or environment and change only through a deploy." actions={<LiveBadge />} />
      <CardBody className="space-y-3 text-sm">
        {a.data && (
          <>
            <table className="w-full"><caption className="sr-only">Rate limits</caption><tbody className="divide-y divide-border">{a.data.rate_limits.map((r) => <tr key={r.name}><td className="py-2 text-text">{r.name}</td><td className="tnum py-2 text-right text-muted">{r.limit} / {r.window_seconds >= 60 ? `${r.window_seconds / 60} min` : `${r.window_seconds}s`}</td></tr>)}</tbody></table>
            <p className="text-muted">Account lockout after {a.data.login.lockout_threshold} failures for {Math.round(a.data.login.lockout_seconds / 60)} min. OTP: {a.data.otp.max_attempts} attempts. Incident detection: {a.data.incident_detection.failed_login_surge_threshold}+ failures in {a.data.incident_detection.window_minutes} min.</p>
          </>
        )}
      </CardBody>
    </Card>
  );
}

function Overview() {
  const o = useApi<Overview>("/security/overview");
  const open = Object.values(o.data?.open_events ?? {}).reduce((a, b) => a + b, 0);
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <KpiCard label="Open incidents" value={String(open)} hint={`${o.data?.open_events.critical ?? 0} critical · ${o.data?.open_events.high ?? 0} high`} />
      <KpiCard label="Active sessions" value={String(o.data?.active_sessions ?? "—")} />
      <KpiCard label="Active API keys" value={String(o.data?.active_api_keys ?? "—")} />
      <KpiCard label="Dead-letter webhooks" value={String(o.data?.dead_webhook_deliveries ?? "—")} hint={`${o.data?.alert_routes ?? 0} alert routes`} />
    </div>
  );
}

export function SecurityConsole() {
  return (
    <RequirePerm perm="security:read">
      <PageHeader title="Security" description="Incidents, audit trail, API keys, webhooks and sessions. No compliance certification is claimed; see docs/security." />
      <div className="mb-4"><Overview /></div>
      <Tabs defaultValue="events">
        <TabsList className="overflow-x-auto">
          <TabsTrigger value="events">Incidents</TabsTrigger>
          <TabsTrigger value="audit">Audit log</TabsTrigger>
          <TabsTrigger value="developer">API keys &amp; webhooks</TabsTrigger>
          <TabsTrigger value="sessions">Sessions</TabsTrigger>
          <TabsTrigger value="abuse">Abuse controls</TabsTrigger>
        </TabsList>
        <TabsContent value="events" className="space-y-4 pt-4"><Events /><Routes /></TabsContent>
        <TabsContent value="audit" className="pt-4"><AuditTable /></TabsContent>
        <TabsContent value="developer" className="space-y-4 pt-4"><ApiKeysPanel /><WebhooksPanel /></TabsContent>
        <TabsContent value="sessions" className="pt-4"><Sessions /></TabsContent>
        <TabsContent value="abuse" className="pt-4"><Abuse /></TabsContent>
      </Tabs>
    </RequirePerm>
  );
}
