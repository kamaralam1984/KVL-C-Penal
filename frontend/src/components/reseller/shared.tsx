"use client";

import * as React from "react";
import { api } from "@/lib/http";
import { cn } from "@/lib/cn";

export const RESOURCES = ["websites", "domains", "databases", "apps", "disk_mb", "bandwidth_gb"] as const;
export const label = (r: string) => r.replace("_", " ");

export function Meter({ used, limit, name }: { used: number; limit: number; name: string }) {
  const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
  return (
    <div role="progressbar" aria-label={name} aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} className="h-1.5 overflow-hidden rounded-full bg-subtle">
      <div className={cn("h-full rounded-full", pct >= 90 ? "bg-danger" : pct >= 75 ? "bg-warning" : "bg-primary")} style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Fetch an authenticated text/CSV/HTML response and hand it to the browser as a file download. */
export async function downloadFile(path: string, filename: string, type = "text/csv") {
  const text = await api<string>(path);
  const url = URL.createObjectURL(new Blob([typeof text === "string" ? text : JSON.stringify(text)], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
