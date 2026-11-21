"use client";

import Link from "next/link";
import { ArrowRight, Bot, Cloud, Database, Globe, Server, Shield } from "lucide-react";

/** Glowing "operations centre" strip for the admin dashboard. Every number comes from /stats/dashboard. */
function Ring({ value, label, sub, tone = "var(--chart-1)" }: { value: number | null; label: string; sub: string; tone?: string }) {
  const r = 34, c = 2 * Math.PI * r;
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div className="glow-card flex items-center gap-4 p-4">
      <svg viewBox="0 0 84 84" className="h-20 w-20 shrink-0 -rotate-90" role="img" aria-label={`${label}: ${value == null ? "no data" : `${pct.toFixed(1)}%`}`}>
        <circle cx="42" cy="42" r={r} fill="none" stroke="var(--subtle)" strokeWidth="8" />
        <circle cx="42" cy="42" r={r} fill="none" stroke={tone} strokeWidth="8" strokeLinecap="round" strokeDasharray={`${(pct / 100) * c} ${c}`} style={{ filter: `drop-shadow(0 0 6px ${tone})`, transition: "stroke-dasharray .8s ease" }} />
      </svg>
      <div className="min-w-0">
        <p className="text-xs text-muted">{label}</p>
        <p className="text-2xl font-semibold tabular-nums text-text">{value == null ? "—" : `${pct.toFixed(pct === 100 || pct === 0 ? 0 : 1)}%`}</p>
        <p className="truncate text-xs text-faint">{sub}</p>
      </div>
    </div>
  );
}

function Node({ icon: Icon, label, value }: { icon: React.ComponentType<{ className?: string }>; label: string; value: string }) {
  return (
    <div className="flex flex-col items-center gap-1 text-center">
      <span className="grid h-11 w-11 place-items-center rounded-full border border-[var(--primary)]/50 bg-[var(--primary-soft)] shadow-[0_0_14px_var(--primary-soft)]"><Icon className="h-5 w-5 text-primary" /></span>
      <span className="text-xs font-medium text-text">{label}</span>
      <span className="text-[11px] text-muted tabular-nums">{value}</span>
    </div>
  );
}

const Arrow = () => <span aria-hidden className="mt-[-1.4rem] h-px flex-1 bg-gradient-to-r from-[var(--primary)]/20 via-[var(--primary)] to-[var(--primary)]/20" />;

export function OpsHero({ servers, alerts, websites, domains, apps, databases, ticketsOpen }: {
  servers?: { total: number; connected: number }; alerts: number; websites: number; domains: number; apps: number; databases: number; ticketsOpen: number;
}) {
  const online = servers && servers.total ? (100 * servers.connected) / servers.total : null;
  const health = servers && servers.total ? Math.max(0, 100 - (100 * Math.min(alerts, servers.total * 4)) / (servers.total * 4)) : null;
  return (
    <section aria-label="Operations centre" className="space-y-3">
      <div className="grid gap-3 md:grid-cols-3">
        <Ring value={online} label="Servers online" sub={servers?.total ? `${servers.connected} of ${servers.total} reporting` : "no server registered yet"} />
        <Ring value={health} label="Health score" sub={`${alerts} open alert${alerts === 1 ? "" : "s"}`} tone={alerts ? "var(--warning)" : "var(--success)"} />
        <Link href="/admin/assistant" className="glow-card group flex items-center gap-4 p-4">
          <span className="grid h-14 w-14 shrink-0 place-items-center rounded-2xl bg-[var(--primary-soft)] shadow-[0_0_18px_var(--primary-soft)]"><Bot className="h-7 w-7 text-primary" /></span>
          <span className="min-w-0">
            <span className="block text-sm font-semibold text-text">Ask the AI assistant</span>
            <span className="block text-xs text-muted">Report · Investigate · Troubleshoot{ticketsOpen ? ` · ${ticketsOpen} open tickets` : ""}</span>
          </span>
          <ArrowRight className="ml-auto h-4 w-4 text-primary transition-transform group-hover:translate-x-1" />
        </Link>
      </div>
      <div className="glow-card p-4">
        <p className="mb-3 text-sm font-semibold text-text">Request path</p>
        <div className="flex items-start gap-2 overflow-x-auto pb-1">
          <Node icon={Globe} label="Visitors" value={`${domains} domains`} />
          <Arrow />
          <Node icon={Cloud} label="Cloudflare" value="TLS · tunnel" />
          <Arrow />
          <Node icon={Shield} label="Nginx" value={`${websites} sites`} />
          <Arrow />
          <Node icon={Server} label="Apps / PHP" value={`${apps} apps`} />
          <Arrow />
          <Node icon={Database} label="Databases" value={`${databases} DBs`} />
        </div>
      </div>
    </section>
  );
}
