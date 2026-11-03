"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { Select } from "./input";
import { IconButton } from "./button";

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  onPageSize,
  pageSizes = [5, 10, 25],
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (p: number) => void;
  onPageSize: (s: number) => void;
  pageSizes?: number[];
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <nav aria-label="Pagination" className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm text-muted">
      <span className="tnum">
        {from}–{to} of {total}
      </span>
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2">
          Rows
          <Select
            aria-label="Rows per page"
            className="h-8 w-20"
            value={pageSize}
            onChange={(e) => onPageSize(Number(e.target.value))}
          >
            {pageSizes.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </label>
        <span className="tnum">
          Page {page} / {pages}
        </span>
        <div className="flex gap-1">
          <IconButton label="Previous page" variant="outline" disabled={page <= 1} onClick={() => onPage(page - 1)}>
            <ChevronLeft className="h-4 w-4" />
          </IconButton>
          <IconButton label="Next page" variant="outline" disabled={page >= pages} onClick={() => onPage(page + 1)}>
            <ChevronRight className="h-4 w-4" />
          </IconButton>
        </div>
      </div>
    </nav>
  );
}
