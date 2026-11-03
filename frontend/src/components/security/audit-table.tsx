"use client";

import * as React from "react";
import { Card, CardBody } from "@/components/ui/card";
import { Input, SearchInput } from "@/components/ui/input";
import { Pagination } from "@/components/ui/pagination";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { LiveBadge, fmtDate } from "@/components/resource/helpers";
import { useApi } from "@/hooks/use-api";

interface Row { id: string; created_at: string; action: string; actor_user_id: string | null; tenant_id: string | null; target_type: string | null; target_id: string | null; ip: string | null; request_id: string | null; detail: Record<string, unknown> | null }

/** Server-filtered, paginated audit log (needs audit:read, platform only). */
export function AuditTable() {
  const [action, setAction] = React.useState("");
  const [actor, setActor] = React.useState("");
  const [tenant, setTenant] = React.useState("");
  const [from, setFrom] = React.useState("");
  const [to, setTo] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [size, setSize] = React.useState(25);
  const [open, setOpen] = React.useState<string | null>(null);
  const uuid = (v: string) => (/^[0-9a-f-]{36}$/i.test(v) ? v : "");
  const { data, error, loading, reload } = useApi<{ total: number; items: Row[] }>("/security/audit", {
    action, actor_user_id: uuid(actor), tenant_id: uuid(tenant), created_from: from ? new Date(from).toISOString() : "", created_to: to ? new Date(to).toISOString() : "", limit: size, offset: (page - 1) * size,
  });
  const reset = <T,>(set: (v: T) => void) => (v: T) => { set(v); setPage(1); };
  return (
    <Card className="overflow-hidden">
      <CardBody className="flex flex-wrap items-end gap-2 pt-4">
        <SearchInput value={action} onChange={reset(setAction)} placeholder="Action prefix (e.g. support.)" label="Action filter" className="min-w-48 flex-1" />
        <Input aria-label="Actor user id" className="w-60" placeholder="Actor user id" value={actor} onChange={(e) => reset(setActor)(e.target.value.trim())} />
        <Input aria-label="Tenant id" className="w-60" placeholder="Tenant id" value={tenant} onChange={(e) => reset(setTenant)(e.target.value.trim())} />
        <Input aria-label="From" type="datetime-local" className="w-48" value={from} onChange={(e) => reset(setFrom)(e.target.value)} />
        <Input aria-label="To" type="datetime-local" className="w-48" value={to} onChange={(e) => reset(setTo)(e.target.value)} />
        <LiveBadge />
      </CardBody>
      {loading && <p className="px-4 pb-4 text-sm text-muted">Loading…</p>}
      {error && <ErrorState onRetry={reload} />}
      {data?.items.length === 0 && <EmptyState title="No audit events match" />}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">Audit log</caption>
          <thead className="bg-subtle text-left text-xs text-muted"><tr><th className="px-4 py-2">When</th><th className="px-4 py-2">Action</th><th className="hidden px-4 py-2 md:table-cell">Actor</th><th className="hidden px-4 py-2 md:table-cell">Target</th><th className="hidden px-4 py-2 lg:table-cell">IP</th></tr></thead>
          <tbody className="divide-y divide-border">
            {data?.items.map((r) => (
              <React.Fragment key={r.id}>
                <tr className="cursor-pointer hover:bg-subtle" onClick={() => setOpen(open === r.id ? null : r.id)}>
                  <td className="tnum px-4 py-2 whitespace-nowrap">{fmtDate(r.created_at)}</td>
                  <td className="px-4 py-2 font-medium text-text">{r.action}</td>
                  <td className="hidden px-4 py-2 font-mono text-xs md:table-cell">{r.actor_user_id?.slice(0, 8) ?? "system"}</td>
                  <td className="hidden px-4 py-2 md:table-cell">{r.target_type ?? "—"} {r.target_id?.slice(0, 8)}</td>
                  <td className="hidden px-4 py-2 lg:table-cell">{r.ip ?? "—"}</td>
                </tr>
                {open === r.id && <tr><td colSpan={5} className="bg-subtle px-4 py-2"><pre className="overflow-x-auto text-xs">{JSON.stringify({ request_id: r.request_id, tenant_id: r.tenant_id, detail: r.detail }, null, 2)}</pre></td></tr>}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {data && <Pagination page={page} pageSize={size} total={data.total} onPage={setPage} onPageSize={(s) => { setSize(s); setPage(1); }} pageSizes={[25, 50, 100]} />}
    </Card>
  );
}
