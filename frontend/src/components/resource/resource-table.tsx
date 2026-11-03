"use client";

import * as React from "react";
import { Plus, RefreshCw } from "lucide-react";
import { DataTable, type Column, type FilterDef } from "@/components/ui/data-table";
import { Button, IconButton } from "@/components/ui/button";
import { LiveBadge } from "./helpers";
import { useApi } from "@/hooks/use-api";

type Row = { id: string };

/**
 * List screen backed by a GET endpoint. Accepts either an array or `{items: [...]}` responses.
 * Search/sort/filter/pagination run client-side over up to `limit` rows (server paging is used by
 * endpoints that return `total`).
 */
export function ResourceTable<T extends Row>({
  path,
  query,
  columns,
  searchText,
  caption,
  filters,
  rowActions,
  onCreate,
  createLabel = "Create",
  canCreate = true,
  emptyTitle,
  emptyDescription,
  refreshMs,
  toolbarExtra,
}: {
  path: string;
  query?: Record<string, string | number | undefined>;
  columns: Column<T>[];
  searchText: (r: T) => string;
  caption: string;
  filters?: FilterDef<T>[];
  rowActions?: (r: T, reload: () => void) => React.ReactNode;
  onCreate?: () => void;
  createLabel?: string;
  canCreate?: boolean;
  emptyTitle?: string;
  emptyDescription?: string;
  refreshMs?: number;
  toolbarExtra?: React.ReactNode;
}) {
  const { data, error, loading, reload } = useApi<T[] | { items: T[] }>(path, { limit: 100, ...query }, { refreshMs });
  const rows = Array.isArray(data) ? data : (data?.items ?? []);
  return (
    <DataTable
      caption={caption}
      rows={rows}
      columns={columns}
      filters={filters}
      searchText={searchText}
      searchPlaceholder={`Search ${caption.toLowerCase()}…`}
      loading={loading}
      error={!!error}
      onRetry={reload}
      emptyTitle={emptyTitle ?? `No ${caption.toLowerCase()} yet`}
      emptyDescription={emptyDescription}
      rowActions={rowActions ? (r) => rowActions(r, reload) : undefined}
      toolbarExtra={
        <>
          <LiveBadge />
          {toolbarExtra}
          <IconButton label="Refresh" variant="outline" onClick={reload}>
            <RefreshCw className="h-4 w-4" />
          </IconButton>
          {onCreate && canCreate && (
            <Button size="sm" onClick={onCreate}>
              <Plus className="h-4 w-4" /> {createLabel}
            </Button>
          )}
        </>
      }
    />
  );
}
