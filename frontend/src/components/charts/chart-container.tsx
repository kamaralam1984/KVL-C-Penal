"use client";

import * as React from "react";
import { useReducedMotion } from "framer-motion";
import { ResponsiveContainer, type TooltipContentProps } from "recharts";
import type { NameType, ValueType } from "recharts/types/component/DefaultTooltipContent";
import { Card, CardHeader } from "@/components/ui/card";
import { DemoBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/states";

export interface SeriesDef {
  key: string;
  label: string;
  color: string; // a CSS token such as "var(--chart-1)"
}

/** Legend built from real markup so it is keyboard/screen-reader friendly and theme-aware. */
export function ChartLegend({ series }: { series: SeriesDef[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 px-5 pb-1 text-xs text-muted" aria-label="Chart legend">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} aria-hidden />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

export function ChartTooltip({
  active,
  payload,
  label,
  unit,
  series,
}: Partial<TooltipContentProps<ValueType, NameType>> & { unit?: string; series: SeriesDef[] }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-border bg-elevated px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium text-text">{label}</p>
      {payload.map((p) => {
        const def = series.find((s) => s.key === p.dataKey);
        return (
          <p key={String(p.dataKey)} className="flex items-center gap-2 text-muted">
            <span className="h-2 w-2 rounded-sm" style={{ background: def?.color }} aria-hidden />
            {def?.label ?? p.name}
            <span className="tnum ml-auto pl-3 font-medium text-text">
              {p.value}
              {unit}
            </span>
          </p>
        );
      })}
    </div>
  );
}

/** Recharts animates in JS, so reduced motion has to be honoured here rather than in CSS. */
export function useChartAnimation(): boolean {
  const reduce = useReducedMotion();
  return !reduce;
}

export const axisProps = {
  tick: { fill: "var(--muted)", fontSize: 11 },
  tickLine: false,
  axisLine: { stroke: "var(--border)" },
} as const;

/**
 * Standard chart frame: title, unit, optional range control, legend, summary for assistive tech,
 * and a no-data state. Pass `data` so the empty state is handled uniformly.
 */
export function ChartCard({
  title,
  description,
  unit,
  series,
  data,
  actions,
  demo,
  summary,
  height = 240,
  children,
}: {
  title: string;
  description?: string;
  unit?: string;
  series: SeriesDef[];
  data: unknown[];
  actions?: React.ReactNode;
  demo?: boolean;
  /** Plain-language description of the trend for screen readers. */
  summary: string;
  height?: number;
  children: React.ReactElement;
}) {
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            {title}
            {unit && <span className="text-xs font-normal text-muted">({unit})</span>}
            {demo && <DemoBadge />}
          </span>
        }
        description={description}
        actions={actions}
      />
      {data.length === 0 ? (
        <EmptyState title="No data for this range" description="Try a wider time range." className="py-8" />
      ) : (
        <>
          <ChartLegend series={series} />
          <div role="img" aria-label={`${title}. ${summary}`} className="px-2 pb-4" style={{ height }}>
            <ResponsiveContainer width="100%" height="100%">
              {children}
            </ResponsiveContainer>
          </div>
        </>
      )}
    </Card>
  );
}
