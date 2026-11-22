"use client";

import Link from "next/link";
import * as React from "react";
import {
  Activity, ArrowDown, Bell, Boxes, Cloud, Database, Globe, Headphones, KeyRound, LayoutDashboard, LogIn, Network,
  RefreshCw, Server, Settings2, Shield, Trophy, Users, Workflow,
} from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useApi } from "@/hooks/use-api";

/* Full-page operations centre. Every value comes from GET /stats/ops-center; empty data shows "No data yet". */

interface OpsData {
  generated_at: string;
  servers: { total: number; connected: number };
  alerts_open: number;
  traffic: { t: string; rx: number; tx: number; cpu: number | null }[];
  uptime: { percent: number | null; samples: number };
  sessions: { active: number; logins_24h: number[] };
  support: {
    tickets_30d: number; open: number; availability_percent: number | null;
    avg_wait: { day: string; minutes: number | null }[];
    resolution_by_weekday: { day: string; percent: number | null }[];
    agents: { name: string; assigned: number; resolved: number; avg_handle_hours: number | null; sla_percent: number | null }[];
  };
  login_heatmap: number[][];
  jobs: { operation: string; total: number; succeeded: number; failed: number; pending: number; success_percent: number | null; progress_percent: number | null }[];
  timeline: { at: string; action: string; target: string | null; actor: string }[];
  services: { name: string; state: string | null }[];
  monthly: { tenants: { month: string; count: number }[]; tickets: { month: string; count: number }[] };
  disk_forecast: { history: { day: string; disk: number }[]; projection: { day: string; disk: number }[] };
}

const C1 = "var(--chart-1)", C2 = "var(--chart-2)", C3 = "var(--chart-3)", OK = "var(--success)", INFO = "var(--info)";
const axis = { fontSize: 10, fill: "var(--muted)" } as const;
const tip = { contentStyle: { background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 10, fontSize: 12 }, labelStyle: { color: "var(--text)" } };

function Panel({ title, right, className = "", children }: { title: string; right?: React.ReactNode; className?: string; children: React.ReactNode }) {
  return (
    <section className={`glow-card flex min-w-0 flex-col p-4 ${className}`} aria-label={title}>
      <header className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-text">{title}</h2>
        {right}
      </header>
      {children}
    </section>
  );
}

const Empty = ({ h = 120, text = "No data yet" }: { h?: number; text?: string }) => (
  <div className="grid place-items-center rounded-lg border border-dashed border-[var(--border)] text-xs text-faint" style={{ height: h }}>{text}</div>
);

const compact = (n: number) => (n >= 1e6 ? `${Math.round(n / 1e6)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : `${Math.round(n)}`);
const bps = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB/s` : n >= 1e3 ? `${(n / 1e3).toFixed(1)} kB/s` : `${n} B/s`);
const monthLabel = (m: string) => new Date(`${m}-01T00:00:00`).toLocaleString(undefined, { month: "short" });
const ago = (iso: string) => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`;
};

function Ring({ value, tone, caption }: { value: number | null; tone: string; caption: string }) {
  const r = 40, c = 2 * Math.PI * r, pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-1">
      <div className="relative h-28 w-28">
        <svg viewBox="0 0 100 100" className="h-full w-full -rotate-90" role="img" aria-label={`${caption}: ${value == null ? "no data" : `${pct.toFixed(2)}%`}`}>
          <circle cx="50" cy="50" r={r} fill="none" stroke="var(--subtle)" strokeWidth="9" />
          <circle cx="50" cy="50" r={r} fill="none" stroke={tone} strokeWidth="9" strokeLinecap="round" strokeDasharray={`${(pct / 100) * c} ${c}`} style={{ filter: `drop-shadow(0 0 7px ${tone})`, transition: "stroke-dasharray .9s ease" }} />
        </svg>
        <span className="absolute inset-0 grid place-items-center text-xl font-semibold tabular-nums text-text">{value == null ? "—" : `${pct.toFixed(pct >= 99.995 || pct === 0 ? 0 : 2)}%`}</span>
      </div>
      <span className="flex items-center gap-1.5 text-[11px] text-muted"><span className="h-1.5 w-1.5 rounded-full" style={{ background: value == null ? "var(--faint)" : tone }} />{caption}</span>
    </div>
  );
}

function Heatmap({ grid }: { grid: number[][] }) {
  const max = Math.max(1, ...grid.flat());
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  return (
    <div className="flex gap-2">
      <div className="min-w-0 flex-1">
        <div className="grid gap-[3px]" style={{ gridTemplateColumns: "2.2rem repeat(24, minmax(0, 1fr))" }} role="img" aria-label="Sign-ins by weekday and hour (IST), last 30 days">
          {grid.map((row, d) => (
            <React.Fragment key={d}>
              <span className="pr-1 text-right text-[10px] leading-[14px] text-faint">{days[d]}</span>
              {row.map((v, h) => (
                <span key={h} title={`${days[d]} ${String(h).padStart(2, "0")}:00 · ${v} sign-in${v === 1 ? "" : "s"}`} className="h-[14px] rounded-[3px]"
                  style={{ background: v ? `color-mix(in oklab, var(--chart-1) ${18 + (82 * v) / max}%, transparent)` : "var(--subtle)", boxShadow: v / max > 0.6 ? "0 0 6px var(--chart-1)" : undefined }} />
              ))}
            </React.Fragment>
          ))}
          <span />
          {Array.from({ length: 24 }, (_, h) => <span key={h} className="text-center text-[9px] text-faint">{h % 3 === 0 ? h : ""}</span>)}
        </div>
      </div>
      <div className="flex w-3 flex-col items-center">
        <div className="w-2 flex-1 rounded-full" style={{ background: "linear-gradient(to top, var(--subtle), var(--chart-1))" }} />
        <span className="mt-1 text-[9px] text-faint">{max}</span>
      </div>
    </div>
  );
}

const SERVICE_META: Record<string, { label: string; icon: React.ComponentType<{ className?: string }> }> = {
  cloudflared: { label: "Cloudflare tunnel", icon: Cloud },
  nginx: { label: "Nginx", icon: Network },
  "php-fpm": { label: "PHP-FPM", icon: Boxes },
  pm2: { label: "Node (PM2)", icon: Workflow },
  mariadb: { label: "MariaDB", icon: Database },
  redis: { label: "Redis", icon: Database },
};
const stateTone = (s: string | null) => (s === "running" ? OK : s == null || s === "not_configured" ? "var(--faint)" : "var(--danger)");
const stateText = (s: string | null) => (s == null ? "no report" : s.replace("_", " "));

function FlowNode({ icon: Icon, label, state }: { icon: React.ComponentType<{ className?: string }>; label: string; state?: string | null }) {
  const tone = state === undefined ? C1 : stateTone(state);
  return (
    <div className="flex flex-col items-center gap-1 text-center">
      <span className="grid h-11 w-11 place-items-center rounded-full border bg-[var(--primary-soft)]" style={{ borderColor: tone, boxShadow: `0 0 14px color-mix(in oklab, ${tone} 55%, transparent)` }}>
        <Icon className="h-5 w-5 text-primary" />
      </span>
      <span className="text-[11px] font-medium text-text">{label}</span>
      {state !== undefined && <span className="text-[10px]" style={{ color: tone }}>{stateText(state)}</span>}
    </div>
  );
}
const Down = () => <ArrowDown aria-hidden className="mx-auto h-4 w-4 text-primary/70" />;

function RequestFlow({ services }: { services: OpsData["services"] }) {
  const st = (n: string) => services.find((s) => s.name === n)?.state ?? null;
  return (
    <div className="flex flex-col items-center gap-1.5 py-1">
      <FlowNode icon={Globe} label="Visitor" />
      <Down />
      <FlowNode icon={Shield} label="Cloudflare edge" />
      <Down />
      <FlowNode icon={Cloud} label="Tunnel" state={st("cloudflared")} />
      <Down />
      <FlowNode icon={Network} label="Nginx" state={st("nginx")} />
      <div className="relative grid w-full grid-cols-2 gap-3 pt-3 before:absolute before:left-1/4 before:right-1/4 before:top-1 before:h-px before:bg-[var(--primary)]/60">
        <FlowNode icon={Boxes} label="PHP-FPM" state={st("php-fpm")} />
        <FlowNode icon={Workflow} label="Node (PM2)" state={st("pm2")} />
      </div>
      <Down />
      <div className="grid w-full grid-cols-2 gap-3">
        <FlowNode icon={Database} label="MariaDB" state={st("mariadb")} />
        <FlowNode icon={Database} label="Redis" state={st("redis")} />
      </div>
    </div>
  );
}

function ConnectivityMap({ services, servers }: { services: OpsData["services"]; servers: OpsData["servers"] }) {
  // Hub-and-spoke view: the Cloudflare edge in the middle, this iMac server, and each service it runs.
  const nodes = services.map((s, i) => {
    const a = (Math.PI * 2 * i) / services.length - Math.PI / 2;
    return { ...s, x: 300 + Math.cos(a) * 205, y: 150 + Math.sin(a) * 105 };
  });
  const hub = { x: 300, y: 150 };
  const server = { x: 300, y: 150 };
  return (
    <div className="relative">
      <svg viewBox="0 0 600 300" className="h-auto w-full" role="img" aria-label="Network connectivity between Cloudflare, the server and its services">
        <defs>
          <pattern id="dots" width="12" height="12" patternUnits="userSpaceOnUse"><circle cx="1.5" cy="1.5" r="1.2" fill="var(--subtle)" /></pattern>
          <radialGradient id="hubGlow"><stop offset="0%" stopColor="var(--chart-1)" stopOpacity=".55" /><stop offset="100%" stopColor="var(--chart-1)" stopOpacity="0" /></radialGradient>
        </defs>
        <rect width="600" height="300" fill="url(#dots)" rx="12" />
        <circle cx={hub.x} cy={hub.y} r="70" fill="url(#hubGlow)" />
        {nodes.map((n) => {
          const mx = (server.x + n.x) / 2, my = Math.min(server.y, n.y) - 40;
          const tone = stateTone(n.state);
          return (
            <g key={n.name}>
              <path d={`M${server.x},${server.y} Q${mx},${my} ${n.x},${n.y}`} fill="none" stroke={tone} strokeWidth="1.6" strokeDasharray={n.state === "running" ? undefined : "4 4"} style={{ filter: `drop-shadow(0 0 4px ${tone})` }} opacity=".9" />
              <circle cx={n.x} cy={n.y} r="7" fill={tone} style={{ filter: `drop-shadow(0 0 6px ${tone})` }} />
              <text x={n.x} y={n.y + 22} textAnchor="middle" fontSize="11" fill="var(--text)">{SERVICE_META[n.name]?.label ?? n.name}</text>
              <text x={n.x} y={n.y + 35} textAnchor="middle" fontSize="9.5" fill="var(--muted)">{stateText(n.state)}</text>
            </g>
          );
        })}
        <circle cx={server.x} cy={server.y} r="15" fill="var(--chart-1)" style={{ filter: "drop-shadow(0 0 10px var(--chart-1))" }} />
        <text x={server.x} y={server.y + 4} textAnchor="middle" fontSize="11" fontWeight="600" fill="var(--primary-fg)">iMac</text>
      </svg>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
        <li className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: C1 }} />Servers online: {servers.connected}/{servers.total}</li>
        <li className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: OK }} />Running</li>
        <li className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: "var(--danger)" }} />Stopped / failed</li>
        <li className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: "var(--faint)" }} />No report from agent</li>
      </ul>
    </div>
  );
}

const ACTION_ICON: [RegExp, React.ComponentType<{ className?: string }>][] = [
  [/^auth\.|login/, LogIn], [/ticket|support/, Headphones], [/api_?key|security/, KeyRound], [/website|domain|dns|ssl/, Globe],
  [/server|agent|job/, Server], [/setting/, Settings2], [/tenant|user|member/, Users],
];
const actionIcon = (a: string) => ACTION_ICON.find(([re]) => re.test(a))?.[1] ?? Activity;
const prettyAction = (a: string) => a.replace(/[._]/g, " ").replace(/^\w/, (c) => c.toUpperCase());

const TABS = [
  { href: "/admin/ops-center", label: "Overview", icon: LayoutDashboard },
  { href: "/admin/websites", label: "Websites", icon: Globe },
  { href: "/admin/monitoring", label: "Monitoring", icon: Activity },
  { href: "/admin/support", label: "Support", icon: Headphones },
  { href: "/admin/customers", label: "Customers", icon: Users },
  { href: "/admin/servers", label: "Servers", icon: Server },
];

export function OpsCenterPage() {
  const { data: d, error, loading, reload } = useApi<OpsData>("/stats/ops-center", undefined, { refreshMs: 30_000 });

  if (error) return <ErrorState title="Could not load the operations centre" description={error.message} onRetry={reload} />;
  if (loading || !d) return <div className="grid gap-4 lg:grid-cols-4">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-56 rounded-2xl" />)}</div>;

  const s = d.support;
  const wait = s.avg_wait.map((w) => ({ ...w, minutes: w.minutes ?? 0 }));
  const res = s.resolution_by_weekday.map((w) => ({ ...w, percent: w.percent ?? 0 }));
  const spark = d.sessions.logins_24h.map((v, i) => ({ i, v }));
  const forecast = [
    ...d.disk_forecast.history.map((h) => ({ day: h.day.slice(5), used: h.disk, projected: null as number | null })),
    ...d.disk_forecast.projection.map((h) => ({ day: h.day.slice(5), used: null as number | null, projected: h.disk })),
  ];
  if (d.disk_forecast.history.length && d.disk_forecast.projection.length) forecast[d.disk_forecast.history.length - 1].projected = d.disk_forecast.history.at(-1)!.disk;
  const tenants = d.monthly.tenants.map((m) => ({ m: monthLabel(m.month), v: m.count }));
  const tickets = d.monthly.tickets.map((m) => ({ m: monthLabel(m.month), v: m.count }));
  const leaders = [...s.agents].sort((a, b) => b.resolved - a.resolved).slice(0, 3);

  return (
    <div className="ops-theme space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-text sm:text-2xl">Unified Hosting Operations Dashboard</h1>
          <p className="text-xs text-muted">Live from the panel database · refreshes every 30 s · updated {ago(d.generated_at)}</p>
        </div>
        <div className="flex items-center gap-2">
          <Link href="/admin/monitoring" className="glow-card flex items-center gap-1.5 px-3 py-1.5 text-xs text-text"><Bell className="h-3.5 w-3.5 text-primary" />{d.alerts_open} open alert{d.alerts_open === 1 ? "" : "s"}</Link>
          <button type="button" onClick={reload} className="glow-card flex items-center gap-1.5 px-3 py-1.5 text-xs text-text"><RefreshCw className="h-3.5 w-3.5 text-primary" />Refresh</button>
        </div>
      </div>

      <nav aria-label="Operations sections" className="flex gap-1 overflow-x-auto rounded-xl border border-[var(--border)] bg-[var(--surface)] p-1">
        {TABS.map((t, i) => (
          <Link key={t.href} href={t.href} aria-current={i === 0 ? "page" : undefined}
            className={`flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs ${i === 0 ? "bg-[var(--primary-soft)] font-medium text-text shadow-[0_0_12px_var(--primary-soft)]" : "text-muted hover:text-text"}`}>
            <t.icon className="h-3.5 w-3.5" />{t.label}
          </Link>
        ))}
      </nav>

      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
        {/* ---------------- left column */}
        <div className="flex min-w-0 flex-col gap-4">
          <Panel title="Real-Time Analytics Panel" right={<span className="rounded-md border border-[var(--border)] px-2 py-0.5 text-[10px] text-muted">Last 24 hours</span>}>
            <p className="mb-1 text-xs text-muted">Network in / out</p>
            {d.traffic.length ? (
              <div className="h-36"><ResponsiveContainer>
                <AreaChart data={d.traffic} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id="rx" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={C1} stopOpacity={0.45} /><stop offset="100%" stopColor={C1} stopOpacity={0} /></linearGradient>
                    <linearGradient id="tx" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={C2} stopOpacity={0.35} /><stop offset="100%" stopColor={C2} stopOpacity={0} /></linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--subtle)" vertical={false} />
                  <XAxis dataKey="t" tick={axis} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                  <YAxis tick={axis} tickLine={false} axisLine={false} tickFormatter={(v) => compact(Number(v))} width={36} />
                  <Tooltip {...tip} formatter={(v) => bps(Number(v))} />
                  <Area type="monotone" dataKey="rx" name="In" stroke={C1} strokeWidth={2} fill="url(#rx)" />
                  <Area type="monotone" dataKey="tx" name="Out" stroke={C2} strokeWidth={2} fill="url(#tx)" />
                </AreaChart>
              </ResponsiveContainer></div>
            ) : <Empty h={144} text="No metrics yet — waiting for the server agent" />}

            <div className="mt-3 grid grid-cols-2 gap-3">
              <div>
                <p className="mb-1 text-xs text-muted">Avg first response (min)</p>
                <div className="h-24"><ResponsiveContainer>
                  <BarChart data={wait} margin={{ top: 2, right: 0, bottom: 0, left: -28 }}>
                    <XAxis dataKey="day" tick={axis} tickLine={false} axisLine={false} />
                    <YAxis tick={axis} tickLine={false} axisLine={false} allowDecimals={false} />
                    <Tooltip {...tip} />
                    <Bar dataKey="minutes" name="Minutes" fill={C1} radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer></div>
              </div>
              <div>
                <p className="mb-1 text-xs text-muted">Resolved within SLA (%)</p>
                <div className="h-24"><ResponsiveContainer>
                  <BarChart data={res} margin={{ top: 2, right: 0, bottom: 0, left: -28 }}>
                    <XAxis dataKey="day" tick={axis} tickLine={false} axisLine={false} />
                    <YAxis tick={axis} tickLine={false} axisLine={false} domain={[0, 100]} />
                    <Tooltip {...tip} />
                    <Bar dataKey="percent" name="%" fill={C2} radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer></div>
              </div>
            </div>

            <p className="mb-1.5 mt-3 text-xs text-muted">Peak sign-in times (IST, 30 days)</p>
            <Heatmap grid={d.login_heatmap} />
          </Panel>

          <Panel title="Request Flow Diagram">
            <RequestFlow services={d.services} />
          </Panel>
        </div>

        {/* ---------------- middle (two columns wide) */}
        <div className="flex min-w-0 flex-col gap-4 lg:col-span-1 xl:col-span-2">
          <div className="grid gap-4 sm:grid-cols-3">
            <Panel title="Active Sessions">
              <p className="text-[11px] text-muted">Real time (15 min)</p>
              <p className="text-4xl font-semibold tabular-nums text-text">{d.sessions.active.toLocaleString()}</p>
              <div className="mt-auto h-12"><ResponsiveContainer>
                <AreaChart data={spark} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
                  <defs><linearGradient id="sp" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={C1} stopOpacity={0.5} /><stop offset="100%" stopColor={C1} stopOpacity={0} /></linearGradient></defs>
                  <Area type="monotone" dataKey="v" stroke={C1} strokeWidth={2} fill="url(#sp)" />
                </AreaChart>
              </ResponsiveContainer></div>
              <p className="text-[10px] text-faint">Sign-ins per hour, last 24 h</p>
            </Panel>
            <Panel title="Uptime Percentage">
              <Ring value={d.uptime.percent} tone={OK} caption={d.uptime.percent == null ? "No heartbeats yet" : "Agent heartbeats, 7 days"} />
            </Panel>
            <Panel title="Support Availability">
              <Ring value={s.availability_percent} tone={INFO} caption={s.availability_percent == null ? "No replies yet" : "First reply within SLA, 30 days"} />
            </Panel>
          </div>

          <Panel title="Job Management" right={<span className="rounded-md border border-[var(--border)] px-2 py-0.5 text-[10px] text-muted">Last 7 days</span>}>
            {d.jobs.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[30rem] text-left text-xs">
                  <thead className="text-muted"><tr><th className="pb-2 font-medium">Operation</th><th className="pb-2 font-medium">Progress</th><th className="pb-2 font-medium">Success rate</th><th className="pb-2 text-right font-medium">Action</th></tr></thead>
                  <tbody>
                    {d.jobs.map((j) => (
                      <tr key={j.operation} className="border-t border-[var(--border)]">
                        <td className="py-2.5 pr-2"><span className="border-l-2 border-[var(--primary)] pl-2 font-medium text-text">{j.operation}</span><p className="pl-2.5 text-[10px] text-faint">{j.total} job{j.total === 1 ? "" : "s"} · {j.pending} waiting</p></td>
                        <td className="w-1/3 py-2.5 pr-3">
                          <div className="h-1.5 rounded-full bg-[var(--subtle)]"><div className="h-1.5 rounded-full" style={{ width: `${j.progress_percent ?? 0}%`, background: OK, boxShadow: `0 0 8px ${OK}` }} /></div>
                        </td>
                        <td className="py-2.5 font-semibold tabular-nums" style={{ color: j.success_percent == null ? "var(--faint)" : j.success_percent >= 90 ? OK : "var(--warning)" }}>{j.success_percent == null ? "—" : `${j.success_percent}%`}</td>
                        <td className="py-2.5 text-right"><Link href="/admin/servers" className="rounded-md bg-[var(--primary)] px-2.5 py-1 text-[11px] font-medium text-[var(--primary-fg)] shadow-[0_0_10px_var(--primary-soft)]">View ›</Link></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <Empty h={110} text="No server jobs in the last 7 days" />}
          </Panel>

          <Panel title="Network Connectivity Map">
            <ConnectivityMap services={d.services} servers={d.servers} />
          </Panel>

          <Panel title="Performance Trend Graphs">
            <div className="grid gap-3 md:grid-cols-3">
              <div className="rounded-xl border border-[var(--border)] p-2.5">
                <p className="mb-1 text-xs font-medium text-text">Monthly New Customers</p>
                <div className="h-28"><ResponsiveContainer>
                  <AreaChart data={tenants} margin={{ top: 4, right: 2, bottom: 0, left: -30 }}>
                    <defs><linearGradient id="mt" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={C1} stopOpacity={0.5} /><stop offset="100%" stopColor={C1} stopOpacity={0} /></linearGradient></defs>
                    <XAxis dataKey="m" tick={axis} tickLine={false} axisLine={false} interval={1} />
                    <YAxis tick={axis} tickLine={false} axisLine={false} allowDecimals={false} />
                    <Tooltip {...tip} />
                    <Area type="monotone" dataKey="v" name="New customers" stroke={C1} strokeWidth={2} fill="url(#mt)" />
                  </AreaChart>
                </ResponsiveContainer></div>
              </div>
              <div className="rounded-xl border border-[var(--border)] p-2.5">
                <p className="mb-1 text-xs font-medium text-text">Support Ticket Trends</p>
                <div className="h-28"><ResponsiveContainer>
                  <BarChart data={tickets} margin={{ top: 4, right: 2, bottom: 0, left: -30 }}>
                    <XAxis dataKey="m" tick={axis} tickLine={false} axisLine={false} interval={1} />
                    <YAxis tick={axis} tickLine={false} axisLine={false} allowDecimals={false} />
                    <Tooltip {...tip} />
                    <Bar dataKey="v" name="Tickets" fill={C2} radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer></div>
              </div>
              <div className="rounded-xl border border-[var(--border)] p-2.5">
                <p className="mb-1 text-xs font-medium text-text">Disk Load Forecast</p>
                {forecast.length ? (
                  <div className="h-28"><ResponsiveContainer>
                    <LineChart data={forecast} margin={{ top: 4, right: 2, bottom: 0, left: -30 }}>
                      <XAxis dataKey="day" tick={axis} tickLine={false} axisLine={false} minTickGap={18} />
                      <YAxis tick={axis} tickLine={false} axisLine={false} domain={[0, 100]} />
                      <Tooltip {...tip} formatter={(v) => `${v}%`} />
                      <Line type="monotone" dataKey="used" name="Disk used" stroke={C3} strokeWidth={2} dot={false} connectNulls={false} />
                      <Line type="monotone" dataKey="projected" name="Projected" stroke={C3} strokeWidth={2} strokeDasharray="5 4" dot={false} connectNulls={false} />
                    </LineChart>
                  </ResponsiveContainer></div>
                ) : <Empty h={112} text="Needs a few days of metrics" />}
              </div>
            </div>
          </Panel>
        </div>

        {/* ---------------- right column */}
        <div className="flex min-w-0 flex-col gap-4">
          <Panel title="Support Productivity Tracker">
            {s.agents.length ? (
              <>
                <table className="w-full text-left text-xs">
                  <thead className="text-[10px] text-muted"><tr><th className="pb-1.5 font-medium">Staff</th><th className="pb-1.5 text-right font-medium">Resolved</th><th className="pb-1.5 text-right font-medium">Avg handle</th><th className="pb-1.5 text-right font-medium">SLA</th></tr></thead>
                  <tbody>
                    {s.agents.map((a, i) => (
                      <tr key={a.name + i} className="border-t border-[var(--border)]">
                        <td className="py-2"><span className="mr-1.5 text-faint">{i + 1}</span><span className="text-text">{a.name}</span></td>
                        <td className="py-2 text-right tabular-nums">{a.resolved}<span className="text-faint">/{a.assigned}</span></td>
                        <td className="py-2 text-right tabular-nums">{a.avg_handle_hours == null ? "—" : `${a.avg_handle_hours}h`}</td>
                        <td className="py-2 text-right font-semibold tabular-nums" style={{ color: a.sla_percent == null ? "var(--faint)" : OK }}>{a.sla_percent == null ? "—" : `${a.sla_percent}%`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <ul className="mt-3 space-y-1.5">
                  {leaders.map((a, i) => (
                    <li key={a.name + i} className="flex items-center gap-2 rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-xs">
                      <span className="text-faint">{i + 1}</span><span className="flex-1 text-text">{a.name}</span>
                      <span className="tabular-nums text-text">{a.resolved}</span><Trophy className="h-3.5 w-3.5" style={{ color: "var(--warning)" }} />
                    </li>
                  ))}
                </ul>
              </>
            ) : <Empty h={150} text="No assigned tickets in the last 30 days" />}
            <p className="mt-2 text-[10px] text-faint">{s.tickets_30d} tickets in 30 days · {s.open} open</p>
          </Panel>

          <Panel title="Activity Timeline" className="flex-1">
            {d.timeline.length ? (
              <ol className="relative space-y-3 before:absolute before:bottom-1 before:left-[13px] before:top-1 before:w-px before:bg-[var(--border)]">
                {d.timeline.map((e, i) => {
                  const Icon = actionIcon(e.action);
                  return (
                    <li key={i} className="relative flex gap-2.5">
                      <span className="z-10 grid h-7 w-7 shrink-0 place-items-center rounded-full border border-[var(--primary)]/50 bg-[var(--surface)] shadow-[0_0_10px_var(--primary-soft)]"><Icon className="h-3.5 w-3.5 text-primary" /></span>
                      <div className="min-w-0 flex-1 rounded-lg border border-[var(--border)] px-2.5 py-1.5">
                        <p className="truncate text-xs font-medium text-text">{e.actor}</p>
                        <p className="truncate text-[11px] text-muted">{prettyAction(e.action)}{e.target ? ` · ${e.target}` : ""}</p>
                        <p className="text-[10px] text-faint">{ago(e.at)}</p>
                      </div>
                    </li>
                  );
                })}
              </ol>
            ) : <Empty h={150} text="No activity yet" />}
          </Panel>
        </div>
      </div>
    </div>
  );
}
