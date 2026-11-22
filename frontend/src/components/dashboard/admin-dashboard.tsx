"use client";

import Link from "next/link";
import * as React from "react";
import { animate, motion, useReducedMotion } from "framer-motion";
import {
  Activity, AlertTriangle, ArrowRight, Bot, CheckCircle2, Cloud, CreditCard, Database, Globe, Headphones, HardDrive,
  KeyRound, Lock, Cpu, MemoryStick, RefreshCw, Server, ServerCog, Shield, Sparkles, Users, Zap,
} from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useApi } from "@/hooks/use-api";
import { useAuth } from "@/components/providers/auth-provider";
import { fmtMoney } from "@/components/resource/helpers";

/* Platform overview. Every number comes from GET /stats/dashboard; nothing is invented when data is missing. */

interface Resource {
  server: string; age_seconds: number | null;
  cpu: number | null; mem: number | null; swap: number | null; disk: number | null; load1: number | null; cpu_count: number | null;
  uptime_seconds: number | null; rx_bps: number | null; tx_bps: number | null;
  spark: { t: string; cpu: number | null; mem: number | null }[];
}
interface Stats {
  health: { score: number | null; factors: { label: string; impact: number }[] };
  alerts: { id: string; severity: string; metric: string; message: string; opened_at: string }[];
  resources: Resource[];
  services: { server: string; name: string; state: string; checked_at: string }[];
  tenants: { total: number; by_status: Record<string, number> };
  users: number;
  servers: { total: number; connected: number };
  alerts_open: number;
  websites: number; domains: number; databases: number; apps: number; tickets_open: number;
  subscriptions_active: number; revenue_month_paise: number; certs_expiring_30d: number; jobs_failed_24h: number;
  signups_14d: { day: string; count: number }[];
  recent_activity: { id: string; action: string; created_at: string; target_type: string | null; actor: string | null }[];
}

const axis = { fontSize: 10, fill: "var(--muted)" } as const;
const tip = { contentStyle: { background: "var(--surface-solid)", border: "1px solid var(--border)", borderRadius: 12, fontSize: 12 }, labelStyle: { color: "var(--text)" } };
const ease = [0.16, 1, 0.3, 1] as const;

const tone = (v: number | null | undefined, warn = 75, crit = 90) =>
  v == null ? "var(--faint)" : v >= crit ? "var(--danger)" : v >= warn ? "var(--warning)" : "var(--success)";
const bps = (n: number | null) => {
  if (n == null) return "—";
  const u = ["B/s", "KB/s", "MB/s", "GB/s"]; let i = 0; let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i ? 1 : 0)} ${u[i]}`;
};
const uptime = (s: number | null) => {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
};
const ago = (iso: string) => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "2-digit", month: "short" });
};
const human = (action: string) => {
  const [obj, ...rest] = action.split(".");
  const s = `${obj.replace(/_/g, " ")} ${rest.join(" ").replace(/_/g, " ")}`.trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
};
const actionIcon = (action: string) => {
  const k = action.split(".")[0];
  return ({ server: Server, agent: ServerCog, job: Zap, alert: AlertTriangle, auth: KeyRound, user: Users, tenant: Users, website: Globe, domain: Globe, backup: HardDrive, ticket: Headphones, invoice: CreditCard } as Record<string, React.ComponentType<{ className?: string }>>)[k] ?? Activity;
};

/* ------------------------------------------------------------------ motion helpers */

const container = { hidden: {}, show: { transition: { staggerChildren: 0.06 } } };
const item = { hidden: { opacity: 0, y: 14, scale: 0.98 }, show: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.55, ease } } };

function CountUp({ value, format = (v: number) => Math.round(v).toLocaleString("en-IN") }: { value: number; format?: (v: number) => string }) {
  const reduce = useReducedMotion();
  const [v, setV] = React.useState(0);
  const from = React.useRef(0);
  React.useEffect(() => {
    if (reduce) return;
    const c = animate(from.current, value, { duration: 1.2, ease, onUpdate: setV });
    from.current = value;
    return () => c.stop();
  }, [value, reduce]);
  return <span className="tabular-nums">{format(reduce ? value : v)}</span>;
}

function Card({ children, className = "", as = "div", href }: { children: React.ReactNode; className?: string; as?: "div" | "a"; href?: string }) {
  const body = <motion.div variants={item} className={`dash-card ${className}`}>{children}</motion.div>;
  return as === "a" && href ? <Link href={href} className="block h-full rounded-[1.1rem] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary)]">{body}</Link> : body;
}

function Title({ icon: Icon, children, right }: { icon: React.ComponentType<{ className?: string }>; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      <span className="dash-icon h-7 w-7"><Icon className="h-3.5 w-3.5" /></span>
      <h2 className="text-sm font-semibold text-text">{children}</h2>
      {right && <div className="ml-auto">{right}</div>}
    </div>
  );
}

function LivePill({ label = "Live" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--success)]/30 bg-[var(--success)]/10 px-2.5 py-0.5 text-[10px] font-semibold tracking-widest text-[var(--success)] uppercase">
      <span className="relative flex h-1.5 w-1.5"><span className="dash-ping absolute inline-flex h-full w-full rounded-full bg-[var(--success)]" /><span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[var(--success)]" /></span>
      {label}
    </span>
  );
}

/* ------------------------------------------------------------------ widgets */

function Ring({ value, size = 132, stroke = 11, color, children }: { value: number | null; size?: number; stroke?: number; color: string; children: React.ReactNode }) {
  const reduce = useReducedMotion();
  const r = (size - stroke) / 2, c = 2 * Math.PI * r;
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  const id = React.useId().replace(/:/g, "");
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} className="-rotate-90" width={size} height={size} aria-hidden>
        <defs>
          <linearGradient id={`g${id}`} x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stopColor={color} /><stop offset="100%" stopColor="var(--chart-3)" /></linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--subtle)" strokeWidth={stroke} />
        <motion.circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={`url(#g${id})`} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c - (pct / 100) * c }}
          transition={{ duration: reduce ? 0 : 1.4, ease }} style={{ filter: `drop-shadow(0 0 8px ${color})` }}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">{children}</div>
    </div>
  );
}

function HealthCard({ h, alerts }: { h: Stats["health"]; alerts: number }) {
  const color = h.score == null ? "var(--faint)" : h.score >= 90 ? "var(--success)" : h.score >= 70 ? "var(--warning)" : "var(--danger)";
  return (
    <Card className="p-5">
      <Title icon={Shield} right={<span className="text-[11px] text-muted">{alerts} open alert{alerts === 1 ? "" : "s"}</span>}>Platform health</Title>
      <div className="flex items-center gap-5">
        <Ring value={h.score} color={color}>
          <div>
            <p className="text-3xl font-bold text-text">{h.score == null ? "—" : <CountUp value={h.score} />}</p>
            <p className="text-[10px] tracking-widest text-muted uppercase">score</p>
          </div>
        </Ring>
        <ul className="min-w-0 flex-1 space-y-1.5 text-xs">
          {h.score == null ? <li className="text-muted">No server registered yet.</li>
            : h.factors.length === 0 ? <li className="flex items-center gap-1.5 text-[var(--success)]"><CheckCircle2 className="h-3.5 w-3.5" /> Everything is healthy</li>
            : h.factors.slice(0, 4).map((f, i) => (
              <li key={i} className="flex gap-2">
                <span className="shrink-0 rounded-md bg-[var(--danger)]/12 px-1.5 font-mono text-[10px] leading-5 text-[var(--danger)]">{f.impact}</span>
                <span className="line-clamp-2 text-muted">{f.label}</span>
              </li>
            ))}
        </ul>
      </div>
    </Card>
  );
}

function Meter({ icon: Icon, label, value, hint }: { icon: React.ComponentType<{ className?: string }>; label: string; value: number | null; hint?: string }) {
  const reduce = useReducedMotion();
  const c = tone(value);
  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 text-xs">
        <Icon className="h-3.5 w-3.5 text-muted" />
        <span className="text-muted">{label}</span>
        {hint && <span className="text-faint">· {hint}</span>}
        <span className="ml-auto font-semibold tabular-nums" style={{ color: c }}>{value == null ? "—" : `${value.toFixed(1)}%`}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-[var(--subtle)]">
        <motion.div className="dash-bar h-full rounded-full" style={{ background: `linear-gradient(90deg, ${c}, color-mix(in oklab, ${c} 55%, var(--chart-3)))`, boxShadow: `0 0 10px ${c}` }}
          initial={{ width: 0 }} animate={{ width: `${value ?? 0}%` }} transition={{ duration: reduce ? 0 : 1.2, ease }} />
      </div>
    </div>
  );
}

function ServerCard({ r }: { r: Resource }) {
  const fresh = r.age_seconds != null && r.age_seconds < 180;
  return (
    <Card className="p-5">
      <Title icon={Server} right={fresh ? <LivePill label={`${r.age_seconds}s ago`} /> : <span className="text-[11px] text-[var(--warning)]">{r.age_seconds == null ? "no metrics yet" : "stale"}</span>}>
        {r.server}
      </Title>
      <div className="space-y-3">
        <Meter icon={Cpu} label="CPU" value={r.cpu} hint={r.cpu_count ? `${r.cpu_count} cores · load ${r.load1?.toFixed(2) ?? "—"}` : undefined} />
        <Meter icon={MemoryStick} label="Memory" value={r.mem} />
        <Meter icon={RefreshCw} label="Swap" value={r.swap} />
        <Meter icon={HardDrive} label="Fullest disk" value={r.disk} />
      </div>
      <div className="mt-4 grid grid-cols-3 gap-2 text-center text-[11px]">
        <div className="rounded-lg bg-[var(--subtle)] py-1.5"><p className="text-faint">Uptime</p><p className="font-semibold text-text">{uptime(r.uptime_seconds)}</p></div>
        <div className="rounded-lg bg-[var(--subtle)] py-1.5"><p className="text-faint">In</p><p className="font-semibold text-text">{bps(r.rx_bps)}</p></div>
        <div className="rounded-lg bg-[var(--subtle)] py-1.5"><p className="text-faint">Out</p><p className="font-semibold text-text">{bps(r.tx_bps)}</p></div>
      </div>
    </Card>
  );
}

function AssistantCard({ tickets }: { tickets: number }) {
  return (
    <Card as="a" href="/admin/assistant" className="dash-card-hover group flex h-full flex-col justify-between overflow-hidden p-5">
      <div className="dash-orb" aria-hidden />
      <div className="relative">
        <span className="dash-icon h-12 w-12"><Bot className="h-6 w-6" /></span>
        <p className="mt-4 flex items-center gap-1.5 text-base font-semibold text-text">Ask the AI assistant <Sparkles className="h-4 w-4 text-[var(--chart-3)]" /></p>
        <p className="mt-1 text-xs text-muted">Reports, investigations and troubleshooting across servers, websites and billing.</p>
      </div>
      <p className="relative mt-4 flex items-center gap-1 text-xs font-medium text-[var(--primary)]">
        {tickets ? `${tickets} open ticket${tickets === 1 ? "" : "s"} · ` : ""}Open assistant <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-1" />
      </p>
    </Card>
  );
}

const STATE_COLOR: Record<string, string> = { running: "var(--success)", stopped: "var(--warning)", failed: "var(--danger)" };

function FlowNode({ icon: Icon, label, sub, state }: { icon: React.ComponentType<{ className?: string }>; label: string; sub: string; state: string | null | undefined }) {
  const c = state ? STATE_COLOR[state] ?? "var(--faint)" : "var(--faint)";
  return (
    <div className="flex w-20 shrink-0 flex-col items-center gap-1.5 text-center sm:w-24">
      <span className={`dash-node ${state === "running" ? "dash-node-live" : ""}`} style={{ ["--node" as string]: c, borderStyle: state ? "solid" : "dashed" }}>
        <Icon className="h-5 w-5" />
      </span>
      <span className="text-xs font-medium text-text">{label}</span>
      <span className="text-[10px] text-muted">{sub}</span>
      {state !== undefined && <span className="text-[10px] font-medium" style={{ color: c }}>{state ?? "not monitored"}</span>}
    </div>
  );
}
const FlowLine = ({ live }: { live: boolean }) => <span aria-hidden className={`dash-flow mt-6 ${live ? "" : "opacity-40"}`} />;

function RequestPath({ s }: { s: Stats }) {
  const st = (n: string) => s.services.find((x) => x.name === n)?.state ?? null;
  const nodes = [
    { icon: Globe, label: "Visitors", sub: `${s.domains} domains`, state: undefined },
    { icon: Cloud, label: "Cloudflare", sub: "TLS · tunnel", state: null },
    { icon: Shield, label: "Nginx", sub: `${s.websites} sites`, state: st("nginx") },
    { icon: Server, label: "PHP-FPM", sub: `${s.apps} apps`, state: st("php-fpm") },
    { icon: Database, label: "MariaDB", sub: `${s.databases} DBs`, state: st("mariadb") },
    { icon: Zap, label: "Redis", sub: "cache", state: st("redis") },
  ];
  return (
    <Card className="p-5">
      <Title icon={Activity} right={<span className="text-[11px] text-muted">service state from the server agent</span>}>Request path</Title>
      <div className="flex items-start gap-1 overflow-x-auto pb-1">
        {nodes.map((n, i) => (
          <React.Fragment key={n.label}>
            {i > 0 && <FlowLine live={n.state === "running"} />}
            <FlowNode {...n} />
          </React.Fragment>
        ))}
      </div>
    </Card>
  );
}

function Kpi({ label, value, format, icon: Icon, hint, href, accent = "var(--primary)" }: {
  label: string; value: number; format?: (v: number) => string; icon: React.ComponentType<{ className?: string }>; hint?: string; href: string; accent?: string;
}) {
  return (
    <Card as="a" href={href} className="dash-card-hover group h-full p-4">
      <div className="flex items-start justify-between">
        <p className="text-xs font-medium text-muted">{label}</p>
        <span className="dash-icon h-8 w-8 transition-transform group-hover:scale-110 group-hover:rotate-6" style={{ ["--icon" as string]: accent }}><Icon className="h-4 w-4" /></span>
      </div>
      <p className="mt-2 text-3xl font-bold tracking-tight text-text"><CountUp value={value} format={format} /></p>
      {hint && <p className="mt-1 truncate text-[11px] text-faint">{hint}</p>}
    </Card>
  );
}

function LoadChart({ r }: { r: Resource | undefined }) {
  const data = (r?.spark ?? []).map((p) => ({ t: new Date(p.t).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }), cpu: p.cpu, mem: p.mem }));
  return (
    <Card className="flex flex-col p-5">
      <Title icon={Cpu} right={
        <span className="flex items-center gap-3 text-[11px] text-muted">
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-[var(--chart-1)]" />CPU</span>
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-[var(--chart-3)]" />Memory</span>
          <span className="hidden sm:inline">· last 2 hours</span>
        </span>
      }>Server load{r ? <span className="ml-1 font-normal text-muted">· {r.server}</span> : null}</Title>
      {data.length < 2 ? <p className="grid min-h-48 flex-1 place-items-center text-xs text-muted">Collecting samples from the agent…</p> : (
        <div className="min-h-48 flex-1">
          <ResponsiveContainer>
            <AreaChart data={data} margin={{ top: 6, right: 4, bottom: 0, left: -18 }}>
              <defs>
                <linearGradient id="dcpu" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.5} /><stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} /></linearGradient>
                <linearGradient id="dmem" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--chart-3)" stopOpacity={0.4} /><stop offset="100%" stopColor="var(--chart-3)" stopOpacity={0} /></linearGradient>
              </defs>
              <CartesianGrid stroke="var(--subtle)" vertical={false} />
              <XAxis dataKey="t" tick={axis} tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={40} />
              <YAxis tick={axis} tickLine={false} axisLine={false} domain={[0, 100]} unit="%" />
              <Tooltip {...tip} formatter={(v) => `${Number(v).toFixed(1)}%`} />
              <Area type="monotone" dataKey="cpu" name="CPU" stroke="var(--chart-1)" strokeWidth={2} fill="url(#dcpu)" animationDuration={1200} />
              <Area type="monotone" dataKey="mem" name="Memory" stroke="var(--chart-3)" strokeWidth={2} fill="url(#dmem)" animationDuration={1400} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}

function AlertsCard({ alerts }: { alerts: Stats["alerts"] }) {
  return (
    <Card className="p-5">
      <Title icon={AlertTriangle} right={<Link href="/admin/monitoring" className="text-[11px] font-medium text-[var(--primary)] hover:underline">Monitoring →</Link>}>Open alerts</Title>
      {alerts.length === 0 ? (
        <div className="grid h-40 place-items-center text-center text-xs text-muted">
          <div><CheckCircle2 className="mx-auto mb-2 h-8 w-8 text-[var(--success)]" />All clear — no open alerts.</div>
        </div>
      ) : (
        <ul className="space-y-2">
          {alerts.map((a, i) => {
            const c = a.severity === "critical" ? "var(--danger)" : "var(--warning)";
            return (
              <motion.li key={a.id} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.2 + i * 0.08, ease }}
                className="flex gap-3 rounded-xl border p-3" style={{ borderColor: `color-mix(in oklab, ${c} 35%, transparent)`, background: `color-mix(in oklab, ${c} 8%, transparent)` }}>
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" style={{ color: c }} />
                <div className="min-w-0">
                  <p className="text-xs text-text">{a.message}</p>
                  <p className="mt-0.5 text-[10px] text-faint"><span className="font-semibold uppercase" style={{ color: c }}>{a.severity}</span> · {ago(a.opened_at)}</p>
                </div>
              </motion.li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function SignupsChart({ rows }: { rows: Stats["signups_14d"] }) {
  const data = rows.map((r) => ({ t: new Date(r.day).toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }), n: r.count }));
  const total = rows.reduce((a, r) => a + r.count, 0);
  return (
    <Card className="flex flex-col p-5">
      <Title icon={Users} right={<span className="text-[11px] text-muted">{total} in 14 days</span>}>New customers</Title>
      <div className="min-h-52 flex-1">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 6, right: 4, bottom: 0, left: -22 }}>
            <defs>
              <linearGradient id="dsig" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--chart-2)" /><stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0.6} /></linearGradient>
            </defs>
            <CartesianGrid stroke="var(--subtle)" vertical={false} />
            <XAxis dataKey="t" tick={axis} tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={24} />
            <YAxis tick={axis} tickLine={false} axisLine={false} allowDecimals={false} />
            <Tooltip {...tip} cursor={{ fill: "var(--subtle)" }} />
            <Bar dataKey="n" name="New customers" fill="url(#dsig)" radius={[6, 6, 0, 0]} maxBarSize={28} animationDuration={1100} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

function ActivityCard({ items }: { items: Stats["recent_activity"] }) {
  return (
    <Card className="p-5">
      <Title icon={Activity} right={<LivePill />}>Recent activity</Title>
      {items.length === 0 ? <p className="text-xs text-muted">No activity recorded yet.</p> : (
        <ol className="relative space-y-3 before:absolute before:top-2 before:bottom-2 before:left-[13px] before:w-px before:bg-gradient-to-b before:from-[var(--primary)] before:to-transparent">
          {items.map((a, i) => {
            const Icon = actionIcon(a.action);
            return (
              <motion.li key={a.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 + i * 0.05, ease }} className="relative flex gap-3">
                <span className="dash-icon relative z-10 h-7 w-7 shrink-0"><Icon className="h-3.5 w-3.5" /></span>
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-text" title={a.action}>{human(a.action)}</p>
                  <p className="text-[10px] text-faint">{a.actor ?? "System"} · {ago(a.created_at)}</p>
                </div>
              </motion.li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}

function Greeting({ onRefresh }: { onRefresh: () => void }) {
  const { me } = useAuth();
  const [now, setNow] = React.useState<Date | null>(null);
  React.useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);
  const h = now?.getHours();
  const hello = h == null ? "Welcome back" : h < 5 ? "Working late" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  return (
    <motion.header variants={item} className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <p className="text-xs font-medium tracking-widest text-[var(--chart-2)] uppercase">Platform overview</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-text sm:text-3xl">
          {hello}{me?.user.name ? <>, <span className="dash-gradient-text">{me.user.name.split(" ")[0]}</span></> : null}
        </h1>
        <p className="mt-1 text-sm text-muted">Tenants, infrastructure, revenue and activity — live from the control plane.</p>
      </div>
      <div className="flex items-center gap-2">
        <span className="hidden rounded-full border border-[var(--border)] bg-[var(--subtle)] px-3 py-1 font-mono text-xs text-muted tabular-nums sm:inline" suppressHydrationWarning>
          {now ? now.toLocaleTimeString() : "--:--:--"}
        </span>
        <LivePill />
        <button type="button" onClick={onRefresh} className="dash-icon h-8 w-8 cursor-pointer transition-transform hover:rotate-180" aria-label="Refresh"><RefreshCw className="h-4 w-4" /></button>
      </div>
    </motion.header>
  );
}

/* ------------------------------------------------------------------ page */

export function AdminDashboard() {
  const { data: s, loading, error, reload } = useApi<Stats>("/stats/dashboard", undefined, { refreshMs: 30_000 });
  return (
    <div className="dash-theme">
      <div className="dash-aurora" aria-hidden><span /><span /><span /></div>
      <div className="relative">
        {error ? <ErrorState title="Could not load the dashboard" description="The control-plane API did not answer." onRetry={reload} />
          : loading && !s ? (
            <div className="space-y-4">
              <Skeleton className="h-16" />
              <div className="grid gap-4 lg:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-56" />)}</div>
              <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28" />)}</div>
            </div>
          ) : s && (
            <motion.div variants={container} initial="hidden" animate="show" className="space-y-4">
              <Greeting onRefresh={reload} />
              <div className="grid gap-4 lg:grid-cols-3">
                <HealthCard h={s.health} alerts={s.alerts_open} />
                {s.resources[0] ? <ServerCard r={s.resources[0]} /> : (
                  <Card className="grid place-items-center p-5 text-center text-xs text-muted">
                    <div><Server className="mx-auto mb-2 h-8 w-8 text-faint" />No server is reporting yet.<br /><Link href="/admin/servers" className="text-[var(--primary)] hover:underline">Register a server →</Link></div>
                  </Card>
                )}
                <AssistantCard tickets={s.tickets_open} />
              </div>
              <RequestPath s={s} />
              <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
                <Kpi label="Customers" value={s.tenants.total} icon={Users} href="/admin/customers" hint={`${s.tenants.by_status.active ?? 0} active · ${s.tenants.by_status.trial ?? 0} trial · ${s.users} users`} accent="var(--chart-2)" />
                <Kpi label="Servers online" value={s.servers.connected} format={(v) => `${Math.round(v)}/${s.servers.total}`} icon={ServerCog} href="/admin/servers" hint="agent heartbeat < 3 min" accent="var(--success)" />
                <Kpi label="Websites" value={s.websites} icon={Globe} href="/admin/websites" hint={`${s.domains} domains · ${s.apps} apps · ${s.databases} DBs`} accent="var(--chart-1)" />
                <Kpi label="Revenue this month" value={s.revenue_month_paise} format={(v) => fmtMoney(Math.round(v))} icon={CreditCard} href="/admin/billing" hint={`${s.subscriptions_active} active subscriptions · paid invoices`} accent="var(--chart-3)" />
                <Kpi label="Open alerts" value={s.alerts_open} icon={AlertTriangle} href="/admin/monitoring" hint="from the monitoring engine" accent={s.alerts_open ? "var(--warning)" : "var(--success)"} />
                <Kpi label="Open tickets" value={s.tickets_open} icon={Headphones} href="/admin/support" hint="open + pending" accent="var(--info)" />
                <Kpi label="Certificates ≤30 days" value={s.certs_expiring_30d} icon={Lock} href="/admin/domains" hint="expiring soon" accent={s.certs_expiring_30d ? "var(--warning)" : "var(--success)"} />
                <Kpi label="Failed jobs (24h)" value={s.jobs_failed_24h} icon={Zap} href="/admin/servers" hint="agent jobs" accent={s.jobs_failed_24h ? "var(--danger)" : "var(--success)"} />
              </div>
              <div className="grid gap-4 xl:grid-cols-3">
                <div className="xl:col-span-2"><LoadChart r={s.resources[0]} /></div>
                <AlertsCard alerts={s.alerts} />
              </div>
              <div className="grid gap-4 xl:grid-cols-3">
                <div className="xl:col-span-2"><SignupsChart rows={s.signups_14d} /></div>
                <ActivityCard items={s.recent_activity} />
              </div>
            </motion.div>
          )}
      </div>
    </div>
  );
}
