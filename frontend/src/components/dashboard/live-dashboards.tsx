"use client";

import Link from "next/link";
import { Activity, AlertTriangle, AppWindow, Archive, CalendarClock, CreditCard, Database, Globe, Headphones, Lock, Receipt, ServerCog, Users, Waypoints } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { KpiCard } from "@/components/ui/kpi-card";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { TrendChart } from "@/components/charts/trend-chart";
import { UsageMeters } from "@/components/dashboard/widgets";
import { ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { LiveBadge, StatusPill, fmtDate, fmtMoney } from "@/components/resource/helpers";
import { Button } from "@/components/ui/button";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import { useApi } from "@/hooks/use-api";

interface Stats {
  scope: string;
  tenants?: { total: number; by_status: Record<string, number> };
  users?: number;
  servers?: { total: number; connected: number };
  alerts_open?: number;
  websites: number;
  domains: number;
  databases: number;
  apps: number;
  tickets_open: number;
  subscriptions_active?: number;
  revenue_month_paise?: number;
  certs_expiring_30d: number;
  jobs_failed_24h?: number;
  signups_14d?: { day: string; count: number }[];
  recent_activity?: { id: string; action: string; created_at: string; target_type: string | null }[];
  tenant?: { name: string; status: string; plan_code: string | null; trial_ends_at: string | null };
  subscription?: { status: string } | null;
  quotas?: { resource: string; limit: number; used: number }[];
  invoices_unpaid?: number;
  backups_verified?: number;
  last_backup_at?: string | null;
}

function useStats() {
  return useApi<Stats>("/stats/dashboard", undefined, { refreshMs: 60_000 });
}

function Tiles({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">{children}</div>;
}

function Loading() {
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-28" />
      ))}
    </div>
  );
}

function Activity14({ rows }: { rows: { day: string; count: number }[] }) {
  return (
    <TrendChart
      title="New customers"
      unit="per day"
      description="Sign-ups and tenants created, last 14 days."
      kind="bar"
      data={rows.map((r) => ({ t: new Date(r.day).toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }), signups: r.count }))}
      series={[{ key: "signups", label: "New customers", color: "var(--chart-1)" }]}
      summary={`${rows.reduce((a, r) => a + r.count, 0)} new customers in the last 14 days.`}
    />
  );
}

function ActivityFeed({ items }: { items: NonNullable<Stats["recent_activity"]> }) {
  return (
    <Card>
      <CardHeader title="Recent activity" actions={<LiveBadge />} />
      {items.length === 0 ? (
        <CardBody><p className="text-sm text-muted">No activity recorded yet.</p></CardBody>
      ) : (
        <ol className="border-t border-border">
          {items.map((a) => (
            <li key={a.id} className="flex gap-3 border-b border-border px-5 py-3 last:border-0">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-sm text-text">{a.action}</p>
                <p className="text-xs text-faint">{fmtDate(a.created_at)}{a.target_type ? ` · ${a.target_type}` : ""}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function QuickLinks({ items }: { items: { href: string; label: string; icon: React.ComponentType<{ className?: string }> }[] }) {
  return (
    <Card>
      <CardHeader title="Shortcuts" />
      <CardBody className="grid grid-cols-2 gap-2">
        {items.map((i) => (
          <Link key={i.href} href={i.href} className="flex min-h-11 items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium text-text transition-colors hover:border-primary/50 hover:bg-subtle">
            <AnimatedIcon icon={i.icon} size={16} />
            {i.label}
          </Link>
        ))}
      </CardBody>
    </Card>
  );
}

export function AdminDashboard() {
  const { data: s, loading, error, reload } = useStats();
  return (
    <>
      <PageHeader title="Platform overview" description="Tenants, infrastructure, revenue and activity, live from the control plane." actions={<LiveBadge />} />
      {error ? <ErrorState onRetry={reload} /> : loading || !s ? <Loading /> : (
        <div className="space-y-4">
          <Tiles>
            <KpiCard label="Customers" value={String(s.tenants?.total ?? 0)} icon={Users} hint={`${s.tenants?.by_status.active ?? 0} active · ${s.tenants?.by_status.trial ?? 0} trial`} />
            <KpiCard label="Servers online" value={`${s.servers?.connected ?? 0}/${s.servers?.total ?? 0}`} icon={ServerCog} hint={s.servers?.total ? "agent heartbeat" : "none registered yet"} />
            <KpiCard label="Websites" value={String(s.websites)} icon={Globe} hint={`${s.domains} domains`} />
            <KpiCard label="Revenue this month" value={fmtMoney(s.revenue_month_paise)} icon={CreditCard} hint="verified payments only" />
          </Tiles>
          <Tiles>
            <KpiCard label="Open alerts" value={String(s.alerts_open ?? 0)} icon={AlertTriangle} hint="monitoring" />
            <KpiCard label="Open tickets" value={String(s.tickets_open)} icon={Headphones} />
            <KpiCard label="Certificates expiring ≤30d" value={String(s.certs_expiring_30d)} icon={Lock} />
            <KpiCard label="Failed jobs (24h)" value={String(s.jobs_failed_24h ?? 0)} icon={Activity} deltaGoodWhen="down" />
          </Tiles>
          <div className="grid gap-4 xl:grid-cols-3">
            <div className="xl:col-span-2">{s.signups_14d && <Activity14 rows={s.signups_14d} />}</div>
            <ActivityFeed items={s.recent_activity ?? []} />
          </div>
        </div>
      )}
    </>
  );
}

export function HostingDashboard() {
  const { data: s, loading, error, reload } = useStats();
  return (
    <>
      <PageHeader title="Hosting operations" description="Websites, domains, applications, databases and certificates." actions={<LiveBadge />} />
      {error ? <ErrorState onRetry={reload} /> : loading || !s ? <Loading /> : (
        <div className="space-y-4">
          <Tiles>
            <KpiCard label="Websites" value={String(s.websites)} icon={Globe} />
            <KpiCard label="Domains" value={String(s.domains)} icon={Waypoints} />
            <KpiCard label="Applications" value={String(s.apps)} icon={AppWindow} />
            <KpiCard label="Databases" value={String(s.databases)} icon={Database} />
          </Tiles>
          <Tiles>
            <KpiCard label="Servers online" value={`${s.servers?.connected ?? 0}/${s.servers?.total ?? 0}`} icon={ServerCog} />
            <KpiCard label="Certificates expiring ≤30d" value={String(s.certs_expiring_30d)} icon={Lock} />
            <KpiCard label="Open alerts" value={String(s.alerts_open ?? 0)} icon={AlertTriangle} />
            <KpiCard label="Failed jobs (24h)" value={String(s.jobs_failed_24h ?? 0)} icon={Activity} />
          </Tiles>
          <QuickLinks items={[{ href: "/hosting/websites", label: "Websites", icon: Globe }, { href: "/hosting/applications", label: "Applications", icon: AppWindow }, { href: "/hosting/ssl", label: "SSL & DNS", icon: Lock }, { href: "/hosting/backups", label: "Backups", icon: Archive }]} />
        </div>
      )}
    </>
  );
}

const UNITS: Record<string, string> = { disk_mb: "MB", bandwidth_gb: "GB" };

function GettingStarted({ s }: { s: Stats }) {
  const steps = [
    { done: true, label: "Create your account", href: "/account" },
    { done: s.websites > 0, label: "Add your first website", href: "/customer/websites" },
    { done: s.domains > 0, label: "Connect a domain", href: "/customer/domains" },
    { done: (s.backups_verified ?? 0) > 0, label: "Run and verify a backup", href: "/customer/backups" },
    { done: !!s.subscription, label: "Choose a plan", href: "/customer/billing" },
  ];
  const left = steps.filter((x) => !x.done).length;
  if (left === 0) return null;
  return (
    <Card>
      <CardHeader title="Getting started" description={`${steps.length - left} of ${steps.length} done`} />
      <CardBody>
        <ol className="space-y-1.5">
          {steps.map((st) => (
            <li key={st.label}>
              <Link href={st.href} className="flex min-h-11 items-center gap-3 rounded-lg px-2 py-1.5 text-sm hover:bg-subtle">
                <span aria-hidden className={`grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-semibold ${st.done ? "bg-success/15 text-success" : "border border-border-strong text-faint"}`}>{st.done ? "✓" : ""}</span>
                <span className={st.done ? "text-muted line-through" : "font-medium text-text"}>{st.label}</span>
                <span className="sr-only">{st.done ? "(done)" : "(to do)"}</span>
              </Link>
            </li>
          ))}
        </ol>
      </CardBody>
    </Card>
  );
}

export function CustomerDashboard() {
  const { data: s, loading, error, reload } = useStats();
  return (
    <>
      <PageHeader title="My hosting" description="Your plan, usage, billing and support at a glance." actions={<LiveBadge />} />
      {error ? <ErrorState onRetry={reload} /> : loading || !s || !s.tenant ? <Loading /> : (
        <div className="space-y-4">
          <Card>
            <CardHeader title={s.tenant.name} description="Hosting plan" actions={<StatusPill status={s.tenant.status} />} />
            <CardBody className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xl font-semibold text-text">{s.tenant.plan_code ?? "No plan selected yet"}</p>
                {s.tenant.trial_ends_at && s.tenant.status === "trial" && (
                  <p className="mt-1 flex items-center gap-1.5 text-sm text-muted"><CalendarClock className="h-4 w-4" aria-hidden /> Trial ends {fmtDate(s.tenant.trial_ends_at)}</p>
                )}
                {s.subscription && <p className="mt-1 text-sm text-muted">Subscription: {s.subscription.status}</p>}
              </div>
              <Button asChild variant="outline" size="sm"><Link href="/customer/billing">Manage billing</Link></Button>
            </CardBody>
          </Card>
          <GettingStarted s={s} />
          <Tiles>
            <KpiCard label="Websites" value={String(s.websites)} icon={Globe} />
            <KpiCard label="Databases" value={String(s.databases)} icon={Database} />
            <KpiCard label="Open tickets" value={String(s.tickets_open)} icon={Headphones} />
            <KpiCard label="Unpaid invoices" value={String(s.invoices_unpaid ?? 0)} icon={Receipt} />
          </Tiles>
          <div className="grid gap-4 xl:grid-cols-3">
            <div className="xl:col-span-2">
              <UsageMeters
                title="Plan usage"
                source="live"
                meters={(s.quotas ?? []).map((q) => ({ id: q.resource, label: q.resource.replace(/_/g, " "), used: q.used, limit: q.limit, unit: UNITS[q.resource] ?? "" }))}
              />
            </div>
            <Card>
              <CardHeader title="Protection" />
              <CardBody className="space-y-3 text-sm">
                <p className="flex items-center justify-between"><span className="text-muted">Verified backups</span><span className="tnum font-medium text-text">{s.backups_verified ?? 0}</span></p>
                <p className="flex items-center justify-between"><span className="text-muted">Last backup</span><span className="font-medium text-text">{fmtDate(s.last_backup_at)}</span></p>
                <p className="flex items-center justify-between"><span className="text-muted">Certificates expiring ≤30d</span><span className="tnum font-medium text-text">{s.certs_expiring_30d}</span></p>
              </CardBody>
            </Card>
          </div>
          <QuickLinks items={[{ href: "/customer/websites", label: "Websites", icon: Globe }, { href: "/customer/files", label: "Files", icon: Archive }, { href: "/customer/ssl", label: "SSL", icon: Lock }, { href: "/customer/support", label: "Support", icon: Headphones }]} />
        </div>
      )}
    </>
  );
}
