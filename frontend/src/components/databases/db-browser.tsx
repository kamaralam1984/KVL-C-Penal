"use client";

import * as React from "react";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, KeyRound, RefreshCw, Search, Table2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { fmtBytes } from "@/components/resource/helpers";
import { ApiError, get, post } from "@/lib/http";

interface TableInfo { name: string; rows: number | null; bytes: number | null; type: string }
interface Column { name: string; type: string; nullable: boolean; key: string; default: string | null }
interface Index { index: string; column: string; unique: boolean }
type Cell = string | number | null;

const msg = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong");
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const PAGE = 50;

/** Read-only browser: tables → structure → rows. Every request is a read-only query run by the server agent. */
export function DbBrowser({ dbId }: { dbId: string }) {
  const base = `/databases/${dbId}/browse`;
  const call = React.useCallback(async <T,>(kind: string, body: unknown): Promise<T> => {
    const { job_id } = await post<{ job_id: string }>(`${base}/${kind}`, body);
    for (let i = 0; i < 60; i++) {
      const r = await get<T & { status: string; message?: string }>(`${base}/jobs/${job_id}`);
      if (r.status === "succeeded") return r;
      if (["failed", "timed_out", "cancelled"].includes(r.status)) throw new Error(r.message ?? `Request ${r.status}`);
      await sleep(i < 5 ? 400 : 1000);
    }
    throw new Error("The server agent has not answered yet. Try again in a moment.");
  }, [base]);

  const [tables, setTables] = React.useState<TableInfo[] | null>(null);
  const [table, setTable] = React.useState<string>("");
  const [cols, setCols] = React.useState<Column[]>([]);
  const [idx, setIdx] = React.useState<Index[]>([]);
  const [rows, setRows] = React.useState<Cell[][]>([]);
  const [names, setNames] = React.useState<string[]>([]);
  const [hasMore, setHasMore] = React.useState(false);
  const [offset, setOffset] = React.useState(0);
  const [sort, setSort] = React.useState<{ col: string; dir: "asc" | "desc" } | null>(null);
  const [fCol, setFCol] = React.useState("");
  const [fOp, setFOp] = React.useState<"eq" | "contains">("contains");
  const [fVal, setFVal] = React.useState("");
  const [view, setView] = React.useState<"rows" | "structure">("rows");
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string>();

  async function guard(fn: () => Promise<void>) {
    setBusy(true);
    setErr(undefined);
    try { await fn(); } catch (e) { setErr(msg(e)); } finally { setBusy(false); }
  }
  const loadTables = () => guard(async () => setTables((await call<{ tables: TableInfo[] }>("tables", {})).tables));
  async function loadRows(t: string, off: number, s: typeof sort, filter = true) {
    const body = { table: t, limit: PAGE, offset: off, ...(s ? { order_by: s.col, order_dir: s.dir } : {}), ...(filter && fCol && fVal ? { filter_column: fCol, filter_op: fOp, filter_value: fVal } : {}) };
    const r = await call<{ columns: string[]; rows: Cell[][]; has_more: boolean }>("rows", body);
    setNames(r.columns); setRows(r.rows); setHasMore(r.has_more); setOffset(off);
  }
  const openTable = (t: string) => guard(async () => {
    setTable(t); setSort(null); setFCol(""); setFVal(""); setView("rows");
    const d = await call<{ columns: Column[]; indexes: Index[] }>("describe", { table: t });
    setCols(d.columns); setIdx(d.indexes);
    await loadRows(t, 0, null, false);
  });

  if (!tables) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted">Look at your tables and data without phpMyAdmin. Read-only: nothing can be changed from here.</p>
        {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err}</p>}
        <Button loading={busy} onClick={() => void loadTables()}><Table2 className="h-4 w-4" />Load tables</Button>
      </div>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[14rem_1fr]">
      <aside className="space-y-2">
        <div className="flex items-center justify-between"><h4 className="text-sm font-semibold text-text">Tables ({tables.length})</h4><Button size="sm" variant="ghost" onClick={() => void loadTables()}><RefreshCw className="h-3.5 w-3.5" /><span className="sr-only">Reload tables</span></Button></div>
        <ul className="max-h-[50vh] space-y-0.5 overflow-auto">
          {tables.map((t) => (
            <li key={t.name}>
              <button onClick={() => void openTable(t.name)} className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm ${t.name === table ? "bg-primary-soft text-primary" : "text-text hover:bg-subtle"}`}>
                <span className="truncate">{t.name}{t.type === "view" ? " (view)" : ""}</span>
                <span className="shrink-0 text-xs text-muted tabular-nums">{t.rows ?? "—"}</span>
              </button>
            </li>
          ))}
          {!tables.length && <li className="text-sm text-muted">No tables.</li>}
        </ul>
      </aside>

      <section className="min-w-0 space-y-3">
        {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err}</p>}
        {!table ? <p className="text-sm text-muted">Choose a table.</p> : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <h4 className="mr-auto truncate text-sm font-semibold text-text">{table}{(() => { const t = tables.find((x) => x.name === table); return t?.bytes != null ? <span className="ml-2 font-normal text-muted">{fmtBytes(t.bytes)}</span> : null; })()}</h4>
              <Button size="sm" variant={view === "rows" ? "primary" : "outline"} onClick={() => setView("rows")}>Data</Button>
              <Button size="sm" variant={view === "structure" ? "primary" : "outline"} onClick={() => setView("structure")}>Structure</Button>
            </div>
            {view === "structure" ? (
              <div className="overflow-x-auto rounded-md border border-border">
                <table className="w-full text-sm">
                  <thead className="bg-subtle text-left text-xs text-muted"><tr><th className="px-3 py-2">Column</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Null</th><th className="px-3 py-2">Key</th><th className="px-3 py-2">Default</th></tr></thead>
                  <tbody>{cols.map((c) => <tr key={c.name} className="border-t border-border"><td className="px-3 py-1.5 font-medium text-text">{c.key === "PRI" && <KeyRound className="mr-1 inline h-3 w-3 text-warning" aria-label="primary key" />}{c.name}</td><td className="px-3 py-1.5 font-mono text-xs text-muted">{c.type}</td><td className="px-3 py-1.5">{c.nullable ? "yes" : "no"}</td><td className="px-3 py-1.5">{c.key || "—"}</td><td className="px-3 py-1.5 font-mono text-xs">{c.default ?? "—"}</td></tr>)}</tbody>
                </table>
                {!!idx.length && <p className="border-t border-border px-3 py-2 text-xs text-muted">Indexes: {[...new Set(idx.map((i) => `${i.index}${i.unique ? " (unique)" : ""}`))].join(", ")}</p>}
              </div>
            ) : (
              <>
                <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); void guard(() => loadRows(table, 0, sort)); }}>
                  <Select aria-label="Filter column" value={fCol} onChange={(e) => setFCol(e.target.value)} className="w-40"><option value="">Filter column…</option>{cols.map((c) => <option key={c.name}>{c.name}</option>)}</Select>
                  <Select aria-label="Filter type" value={fOp} onChange={(e) => setFOp(e.target.value as "eq" | "contains")} className="w-32"><option value="contains">contains</option><option value="eq">equals</option></Select>
                  <Input aria-label="Filter value" value={fVal} maxLength={200} onChange={(e) => setFVal(e.target.value)} className="min-w-0 flex-1" placeholder="value" />
                  <Button size="sm" type="submit" loading={busy}><Search className="h-4 w-4" />Apply</Button>
                </form>
                <div className="max-h-[55vh] overflow-auto rounded-md border border-border">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-subtle text-left text-muted">
                      <tr>{names.map((n) => (
                        <th key={n} className="px-2 py-2 whitespace-nowrap">
                          <button className="inline-flex items-center gap-1 font-semibold hover:text-text" onClick={() => { const s = { col: n, dir: sort?.col === n && sort.dir === "asc" ? "desc" as const : "asc" as const }; setSort(s); void guard(() => loadRows(table, 0, s)); }}>
                            {n}{sort?.col === n && (sort.dir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                          </button>
                        </th>
                      ))}</tr>
                    </thead>
                    <tbody>
                      {rows.map((r, i) => <tr key={i} className="border-t border-border align-top">{r.map((v, j) => <td key={j} className="max-w-[24rem] px-2 py-1.5 font-mono break-words whitespace-pre-wrap text-text">{v === null ? <span className="text-faint italic">NULL</span> : String(v)}</td>)}</tr>)}
                      {!rows.length && <tr><td className="px-3 py-4 text-sm text-muted" colSpan={Math.max(1, names.length)}>No rows.</td></tr>}
                    </tbody>
                  </table>
                </div>
                <div className="flex items-center justify-between text-xs text-muted">
                  <span>Rows {rows.length ? offset + 1 : 0}–{offset + rows.length}</span>
                  <div className="flex gap-2">
                    <Button size="sm" variant="outline" disabled={offset === 0 || busy} onClick={() => void guard(() => loadRows(table, Math.max(0, offset - PAGE), sort))}><ChevronLeft className="h-4 w-4" />Previous</Button>
                    <Button size="sm" variant="outline" disabled={!hasMore || busy} onClick={() => void guard(() => loadRows(table, offset + PAGE, sort))}>Next<ChevronRight className="h-4 w-4" /></Button>
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </section>
    </div>
  );
}
