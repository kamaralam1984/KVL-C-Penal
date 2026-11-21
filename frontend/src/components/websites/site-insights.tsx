"use client";

import * as React from "react";
import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { RefreshCw, Search } from "lucide-react";
import { ChartCard, ChartTooltip, axisProps, useChartAnimation, type SeriesDef } from "@/components/charts/chart-container";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/tabs";
import { fmtBytes } from "@/components/resource/helpers";
import { ApiError, get, post } from "@/lib/http";

const msg = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong");
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Start an agent job and poll its result endpoint until it finishes (max ~45 s). */
async function runJob<T extends { status: string; message?: string }>(start: string, body: unknown, result: (id: string) => string): Promise<T> {
  const { job_id } = await post<{ job_id: string }>(start, body);
  for (let i = 0; i < 60; i++) {
    const r = await get<T>(result(job_id));
    if (r.status === "succeeded") return r;
    if (["failed", "timed_out", "cancelled"].includes(r.status)) throw new Error(r.message ?? `Request ${r.status}`);
    await sleep(i < 5 ? 500 : 1000);
  }
  throw new Error("The server agent has not answered yet. Try again in a moment.");
}

interface LogsResult { status: string; message?: string; lines: string[]; has_more: boolean; total_lines: number; page: number }

export function SiteLogs({ websiteId }: { websiteId: string }) {
  const [kind, setKind] = React.useState<"access" | "error">("error");
  const [q, setQ] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [data, setData] = React.useState<LogsResult | null>(null);
  const [err, setErr] = React.useState<string>();
  const [loading, setLoading] = React.useState(false);

  const load = React.useCallback(async (p: number) => {
    setLoading(true);
    setErr(undefined);
    try {
      const body = { kind, page: p, page_size: 200, ...(q.trim() ? { contains: q.trim() } : {}) };
      setData(await runJob<LogsResult>(`/websites/${websiteId}/logs`, body, (id) => `/websites/${websiteId}/logs/${id}`));
      setPage(p);
    } catch (e) {
      setErr(msg(e));
    } finally {
      setLoading(false);
    }
  }, [kind, q, websiteId]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Segmented value={kind} onChange={(v) => { setKind(v); setData(null); }} options={[{ value: "error" as const, label: "Error log" }, { value: "access" as const, label: "Access log" }]} label="Log type" />
        <form className="flex flex-1 items-center gap-2" onSubmit={(e) => { e.preventDefault(); void load(1); }}>
          <Input aria-label="Filter log lines" placeholder="Filter, e.g. 404 or wp-login" value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} className="min-w-0 flex-1" />
          <Button size="sm" type="submit" loading={loading}>{data ? <RefreshCw className="h-4 w-4" /> : <Search className="h-4 w-4" />}{data ? "Refresh" : "Show"}</Button>
        </form>
      </div>
      {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err}</p>}
      {data && (
        <>
          <p className="text-xs text-muted">Newest first · {data.total_lines.toLocaleString()} matching lines in the last 4 MB · secrets are redacted</p>
          <pre className="max-h-[50vh] overflow-auto rounded-md border border-border bg-subtle p-3 font-mono text-[12px] leading-5 text-text whitespace-pre-wrap break-all">{data.lines.length ? data.lines.join("\n") : "No lines."}</pre>
          <div className="flex justify-between">
            <Button size="sm" variant="outline" disabled={page <= 1 || loading} onClick={() => void load(page - 1)}>Newer</Button>
            <Button size="sm" variant="outline" disabled={!data.has_more || loading} onClick={() => void load(page + 1)}>Older</Button>
          </div>
        </>
      )}
    </div>
  );
}

interface StatsResult {
  status: string; message?: string; requests: number; human_requests: number; bot_requests: number; bytes_out: number;
  unique_visitors: number | null; first_seen: string | null; last_seen: string | null; heatmap: number[][];
  daily: [string, number][]; status_codes: Record<string, number>; top_paths: [string, number][]; top_referrers: [string, number][];
}
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Heatmap({ grid }: { grid: number[][] }) {
  const max = Math.max(1, ...grid.flat());
  return (
    <figure className="space-y-1" aria-label="Busiest hours (server time): weekday by hour">
      <figcaption className="text-sm font-semibold text-text">Busiest hours</figcaption>
      <div className="overflow-x-auto">
        <div className="grid min-w-[560px] grid-cols-[2.5rem_repeat(24,minmax(0,1fr))] gap-[2px] text-[10px] text-faint">
          <span />
          {Array.from({ length: 24 }, (_, h) => <span key={h} className="text-center">{h % 3 === 0 ? h : ""}</span>)}
          {grid.map((row, d) => (
            <React.Fragment key={d}>
              <span className="pr-1 text-right leading-5">{DAYS[d]}</span>
              {row.map((v, h) => (
                <span key={h} title={`${DAYS[d]} ${h}:00 — ${v} requests`} className="h-5 rounded-[3px]" style={{ background: v ? `color-mix(in oklab, var(--chart-1) ${Math.round(15 + (v / max) * 85)}%, transparent)` : "var(--subtle)" }} />
              ))}
            </React.Fragment>
          ))}
        </div>
      </div>
    </figure>
  );
}

function TopList({ title, rows, empty }: { title: string; rows: [string, number][]; empty: string }) {
  const max = Math.max(1, ...rows.map((r) => r[1]));
  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold text-text">{title}</h4>
      {!rows.length ? <p className="text-xs text-muted">{empty}</p> : (
        <ul className="space-y-1.5">
          {rows.map(([k, v]) => (
            <li key={k} className="text-xs">
              <div className="flex justify-between gap-2"><span className="truncate text-text" title={k}>{k}</span><span className="tabular-nums text-muted">{v.toLocaleString()}</span></div>
              <div className="mt-0.5 h-1 rounded-full bg-subtle"><div className="h-1 rounded-full bg-[var(--chart-1)]" style={{ width: `${(v / max) * 100}%` }} /></div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function SiteStats({ websiteId }: { websiteId: string }) {
  const anim = useChartAnimation();
  const [data, setData] = React.useState<StatsResult | null>(null);
  const [err, setErr] = React.useState<string>();
  const [loading, setLoading] = React.useState(false);
  async function load() {
    setLoading(true);
    setErr(undefined);
    try {
      setData(await runJob<StatsResult>(`/websites/${websiteId}/stats`, {}, (id) => `/websites/${websiteId}/stats/${id}`));
    } catch (e) {
      setErr(msg(e));
    } finally {
      setLoading(false);
    }
  }
  const series: SeriesDef[] = [{ key: "visits", label: "Requests", color: "var(--chart-1)" }];
  const daily = (data?.daily ?? []).map(([d, n]) => ({ day: d.slice(5), visits: n }));
  const tiles: [string, string][] = data ? [
    ["Requests (humans)", data.human_requests.toLocaleString()],
    ["Unique visitors", data.unique_visitors != null ? data.unique_visitors.toLocaleString() : "n/a"],
    ["Bots / crawlers", data.bot_requests.toLocaleString()],
    ["Data sent", fmtBytes(data.bytes_out)],
  ] : [];
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted">From the site&apos;s recent access log (last 4 MB). Only totals leave the server: no IP addresses are shown.</p>
        <Button size="sm" variant="outline" loading={loading} onClick={() => void load()}><RefreshCw className="h-4 w-4" />{data ? "Refresh" : "Load stats"}</Button>
      </div>
      {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err}</p>}
      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {tiles.map(([k, v]) => <div key={k} className="rounded-lg border border-border p-3"><p className="text-xs text-muted">{k}</p><p className="text-xl font-semibold tabular-nums text-text">{v}</p></div>)}
          </div>
          {data.unique_visitors == null && data.requests > 0 && <p className="text-xs text-muted">Unique visitors need the real client address; requests through the Cloudflare tunnel are logged as 127.0.0.1.</p>}
          <ChartCard title="Requests per day" unit="requests" series={series} data={daily} summary={`${daily.length} days of requests, peak ${Math.max(0, ...daily.map((d) => d.visits))}.`} height={200}>
            <BarChart data={daily} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
              <XAxis dataKey="day" {...axisProps} />
              <YAxis {...axisProps} width={40} allowDecimals={false} />
              <Tooltip content={(p) => <ChartTooltip {...p} series={series} />} cursor={{ fill: "var(--subtle)" }} />
              <Bar dataKey="visits" fill="var(--chart-1)" radius={[3, 3, 0, 0]} isAnimationActive={anim} />
            </BarChart>
          </ChartCard>
          <Heatmap grid={data.heatmap} />
          <div className="grid gap-4 sm:grid-cols-3">
            <TopList title="Top pages" rows={data.top_paths} empty="No page views yet." />
            <TopList title="Top referrers" rows={data.top_referrers} empty="No referrers recorded." />
            <TopList title="Responses" rows={Object.entries(data.status_codes).sort()} empty="No responses yet." />
          </div>
        </>
      )}
    </div>
  );
}
