import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DemoBadge, StatusBadge, type Status } from "@/components/ui/badge";
import { KpiCard } from "@/components/ui/kpi-card";
import { EmptyState } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import type { ActivityItem, DataSource, Kpi, ServerHealth, SimpleRow, UsageMeter } from "@/lib/api/types";

export function KpiGrid({
  kpis,
  icons,
  source,
}: {
  kpis: Kpi[];
  icons: Record<string, React.ComponentType<{ className?: string }>>;
  source: DataSource;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {kpis.map((k) => (
        <KpiCard key={k.id} {...k} icon={icons[k.id]} demo={source === "demo"} />
      ))}
    </div>
  );
}

function Meter({ pct, tone }: { pct: number; tone: string }) {
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      className="h-1.5 w-full overflow-hidden rounded-full bg-subtle"
    >
      <div className={cn("h-full rounded-full transition-[width] duration-300", tone)} style={{ width: `${Math.min(100, pct)}%` }} />
    </div>
  );
}

const toneFor = (pct: number) => (pct >= 90 ? "bg-danger" : pct >= 75 ? "bg-warning" : "bg-primary");

export function UsageMeters({ title, meters, source }: { title: string; meters: UsageMeter[]; source: DataSource }) {
  return (
    <Card>
      <CardHeader title={title} actions={source === "demo" ? <DemoBadge /> : undefined} />
      <CardBody className="space-y-4">
        {meters.map((m) => {
          const pct = (m.used / m.limit) * 100;
          return (
            <div key={m.id}>
              <div className="mb-1.5 flex items-baseline justify-between text-sm">
                <span className="font-medium text-text">{m.label}</span>
                <span className="tnum text-muted">
                  {m.used}
                  {m.unit && ` ${m.unit}`} of {m.limit}
                  {m.unit && ` ${m.unit}`}
                </span>
              </div>
              <Meter pct={pct} tone={toneFor(pct)} />
              <span className="sr-only">{Math.round(pct)} percent used</span>
            </div>
          );
        })}
      </CardBody>
    </Card>
  );
}

export function ServerHealthList({ servers, source }: { servers: ServerHealth[]; source: DataSource }) {
  return (
    <Card>
      <CardHeader
        title="Server health"
        description="Live health needs the server agent (Phase 4)."
        actions={source === "demo" ? <DemoBadge /> : undefined}
      />
      <ul className="divide-y divide-border border-t border-border">
        {servers.map((s) => (
          <li key={s.id} className="px-5 py-3">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-base font-medium text-text">{s.name}</span>
              <StatusBadge status={s.status} />
            </div>
            {s.status === "unknown" ? (
              <p className="mt-1 text-xs text-muted">No heartbeat received. Agent not connected.</p>
            ) : (
              <dl className="mt-2 grid grid-cols-3 gap-3 text-xs">
                {(
                  [
                    ["CPU", s.cpu],
                    ["Memory", s.memory],
                    ["Disk", s.disk],
                  ] as const
                ).map(([k, v]) => (
                  <div key={k}>
                    <div className="flex justify-between text-muted">
                      <dt>{k}</dt>
                      <dd className="tnum font-medium text-text">{v}%</dd>
                    </div>
                    <div className="mt-1">
                      <Meter pct={v} tone={toneFor(v)} />
                    </div>
                  </div>
                ))}
              </dl>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function ActivityList({ items, source }: { items: ActivityItem[]; source: DataSource }) {
  return (
    <Card>
      <CardHeader title="Recent activity" actions={source === "demo" ? <DemoBadge /> : undefined} />
      {items.length === 0 ? (
        <EmptyState title="No recent activity" />
      ) : (
        <ol className="border-t border-border">
          {items.map((a) => (
            <li key={a.id} className="flex gap-3 border-b border-border px-5 py-3 last:border-0">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-sm text-text">
                  <span className="font-medium">{a.actor}</span> <span className="text-muted">{a.action}</span>{" "}
                  <span className="font-medium">{a.target}</span>
                </p>
                <p className="text-xs text-faint">{a.time}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

export function SimpleList({
  title,
  rows,
  source,
  empty,
}: {
  title: string;
  rows: SimpleRow[];
  source: DataSource;
  empty: string;
}) {
  return (
    <Card>
      <CardHeader title={title} actions={source === "demo" ? <DemoBadge /> : undefined} />
      {rows.length === 0 ? (
        <EmptyState title={empty} />
      ) : (
        <ul className="border-t border-border">
          {rows.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-3 border-b border-border px-5 py-3 last:border-0">
              <div className="min-w-0">
                <p className="truncate text-base font-medium text-text">{r.name}</p>
                <p className="truncate text-xs text-muted">{r.detail}</p>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <span className="tnum text-sm text-muted">{r.value}</span>
                <StatusBadge status={r.status as Status} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
