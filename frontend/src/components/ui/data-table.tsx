"use client";

import * as React from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Columns3, SearchX } from "lucide-react";
import { Card } from "./card";
import { Button } from "./button";
import { Select, SearchInput } from "./input";
import { Pagination } from "./pagination";
import { Popover, PopoverContent, PopoverTrigger } from "./menu";
import { EmptyState, ErrorState } from "./states";
import { TableSkeleton } from "./skeleton";
import { DemoBadge } from "./badge";
import { cn } from "@/lib/cn";

export interface Column<T> {
  id: string;
  header: string;
  cell: (row: T) => React.ReactNode;
  /** Value used for sorting; omit to make the column non-sortable. */
  sortValue?: (row: T) => string | number;
  align?: "left" | "right";
  /** Hide below the md breakpoint to keep mobile tables readable. */
  hideOnMobile?: boolean;
  defaultHidden?: boolean;
}

export interface FilterDef<T> {
  id: string;
  label: string;
  options: string[];
  match: (row: T, value: string) => boolean;
}

type SortState = { id: string; dir: "asc" | "desc" } | null;

export function DataTable<T extends { id: string }>({
  rows,
  columns,
  searchText,
  searchPlaceholder = "Search…",
  filters = [],
  rowActions,
  loading,
  error,
  onRetry,
  demo,
  caption,
  emptyTitle = "Nothing here yet",
  emptyDescription,
  toolbarExtra,
}: {
  rows: T[];
  columns: Column<T>[];
  /** Flattened searchable text for a row. */
  searchText: (row: T) => string;
  searchPlaceholder?: string;
  filters?: FilterDef<T>[];
  rowActions?: (row: T) => React.ReactNode;
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
  demo?: boolean;
  caption: string;
  emptyTitle?: string;
  emptyDescription?: string;
  toolbarExtra?: React.ReactNode;
}) {
  const [query, setQuery] = React.useState("");
  const [filterVals, setFilterVals] = React.useState<Record<string, string>>({});
  const [sort, setSort] = React.useState<SortState>(null);
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(5);
  const [hidden, setHidden] = React.useState<Set<string>>(
    () => new Set(columns.filter((c) => c.defaultHidden).map((c) => c.id)),
  );

  const processed = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    let out = rows.filter((r) => (q ? searchText(r).toLowerCase().includes(q) : true));
    for (const f of filters) {
      const v = filterVals[f.id];
      if (v) out = out.filter((r) => f.match(r, v));
    }
    if (sort) {
      const col = columns.find((c) => c.id === sort.id);
      if (col?.sortValue) {
        const sv = col.sortValue;
        out = [...out].sort((a, b) => {
          const x = sv(a);
          const y = sv(b);
          const cmp = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
          return sort.dir === "asc" ? cmp : -cmp;
        });
      }
    }
    return out;
  }, [rows, query, filterVals, sort, columns, filters, searchText]);

  const visible = columns.filter((c) => !hidden.has(c.id));
  const pageRows = processed.slice((page - 1) * pageSize, page * pageSize);
  const filtering = query !== "" || Object.values(filterVals).some(Boolean);

  function toggleSort(id: string) {
    setSort((s) => (s?.id !== id ? { id, dir: "asc" } : s.dir === "asc" ? { id, dir: "desc" } : null));
  }

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
        <SearchInput
          value={query}
          onChange={(v) => {
            setQuery(v);
            setPage(1);
          }}
          placeholder={searchPlaceholder}
          label={`Search ${caption}`}
          className="w-full sm:w-64"
        />
        {filters.map((f) => (
          <Select
            key={f.id}
            aria-label={`Filter by ${f.label}`}
            className="w-auto min-w-32"
            value={filterVals[f.id] ?? ""}
            onChange={(e) => {
              setFilterVals((s) => ({ ...s, [f.id]: e.target.value }));
              setPage(1);
            }}
          >
            <option value="">All {f.label.toLowerCase()}</option>
            {f.options.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </Select>
        ))}
        <div className="ml-auto flex items-center gap-2">
          {demo && <DemoBadge />}
          {toolbarExtra}
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="outline" size="sm">
                <Columns3 className="h-4 w-4" /> Columns
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-52 p-2">
              <fieldset>
                <legend className="px-1.5 pb-1 text-xs font-medium text-faint uppercase">Visible columns</legend>
                {columns.map((c) => (
                  <label key={c.id} className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-subtle">
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-[var(--primary)]"
                      checked={!hidden.has(c.id)}
                      disabled={!hidden.has(c.id) && visible.length === 1}
                      onChange={() =>
                        setHidden((s) => {
                          const n = new Set(s);
                          if (n.has(c.id)) n.delete(c.id);
                          else n.add(c.id);
                          return n;
                        })
                      }
                    />
                    {c.header}
                  </label>
                ))}
              </fieldset>
            </PopoverContent>
          </Popover>
        </div>
      </div>

      {loading ? (
        <TableSkeleton cols={visible.length} />
      ) : error ? (
        <ErrorState onRetry={onRetry} />
      ) : processed.length === 0 ? (
        filtering ? (
          <div className="flex flex-col items-center py-10">
            <SearchX className="mb-2 h-6 w-6 text-faint" aria-hidden />
            <p className="text-sm text-muted">No results match your search or filters.</p>
            <Button
              variant="ghost"
              size="sm"
              className="mt-2"
              onClick={() => {
                setQuery("");
                setFilterVals({});
              }}
            >
              Clear filters
            </Button>
          </div>
        ) : (
          <EmptyState title={emptyTitle} description={emptyDescription} />
        )
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-base">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr className="border-b border-border bg-subtle/60 text-xs font-medium tracking-wide text-muted uppercase">
                {visible.map((c) => {
                  const active = sort?.id === c.id;
                  const SortIcon = !active ? ArrowUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
                  return (
                    <th
                      key={c.id}
                      scope="col"
                      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                      className={cn("px-4 py-2.5 whitespace-nowrap", c.align === "right" && "text-right", c.hideOnMobile && "hidden md:table-cell")}
                    >
                      {c.sortValue ? (
                        <button
                          type="button"
                          onClick={() => toggleSort(c.id)}
                          className="inline-flex items-center gap-1 uppercase hover:text-text"
                        >
                          {c.header}
                          <SortIcon className={cn("h-3 w-3", active ? "text-primary" : "text-faint")} aria-hidden />
                        </button>
                      ) : (
                        c.header
                      )}
                    </th>
                  );
                })}
                {rowActions && (
                  <th scope="col" className="w-12 px-4 py-2.5">
                    <span className="sr-only">Actions</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {pageRows.map((row) => (
                <tr key={row.id} className="transition-colors hover:bg-subtle/50">
                  {visible.map((c) => (
                    <td
                      key={c.id}
                      className={cn("px-4 py-3", c.align === "right" && "tnum text-right", c.hideOnMobile && "hidden md:table-cell")}
                    >
                      {c.cell(row)}
                    </td>
                  ))}
                  {rowActions && <td className="px-4 py-2 text-right">{rowActions(row)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && !error && processed.length > 0 && (
        <div className="border-t border-border">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={processed.length}
            onPage={setPage}
            onPageSize={(s) => {
              setPageSize(s);
              setPage(1);
            }}
          />
        </div>
      )}
    </Card>
  );
}
