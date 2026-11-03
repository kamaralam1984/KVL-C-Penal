"use client";

import * as React from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard, ChartTooltip, axisProps, useChartAnimation, type SeriesDef } from "./chart-container";
import { DateRangeSelector, type RangeKey } from "@/components/ui/date-range";
import type { TimePoint } from "@/lib/api/types";

const DEMO_RANGES: { value: RangeKey; label: string }[] = [
  { value: "7d", label: "7d" },
  { value: "14d", label: "14d" },
];

/**
 * Time-series card used by every dashboard. `kind` picks the mark; the frame (title, unit, legend,
 * tooltip, range control, empty state, screen-reader summary) is identical everywhere.
 */
export function TrendChart({
  title,
  description,
  unit,
  series,
  data,
  kind = "area",
  stacked,
  demo,
  summary,
  yMax,
}: {
  title: string;
  description?: string;
  unit?: string;
  series: SeriesDef[];
  data: TimePoint[];
  kind?: "area" | "line" | "bar";
  stacked?: boolean;
  demo?: boolean;
  summary: string;
  yMax?: number;
}) {
  const anim = useChartAnimation();
  const [range, setRange] = React.useState<RangeKey>("14d");
  const points = range === "7d" ? data.slice(-7) : data;

  const common = { data: points, margin: { top: 8, right: 12, bottom: 0, left: 0 } };
  const grid = <CartesianGrid stroke="var(--chart-grid)" vertical={false} />;
  const xAxis = <XAxis dataKey="t" {...axisProps} interval="preserveStartEnd" minTickGap={24} />;
  const yAxis = <YAxis {...axisProps} axisLine={false} width={44} domain={[0, yMax ?? "auto"]} />;
  const tip = <Tooltip content={(p) => <ChartTooltip {...p} unit={unit ? ` ${unit}` : ""} series={series} />} cursor={{ stroke: "var(--border-strong)" }} />;

  return (
    <ChartCard
      title={title}
      description={description}
      unit={unit}
      series={series}
      data={points}
      demo={demo}
      summary={summary}
      actions={<DateRangeSelector value={range} onChange={setRange} options={DEMO_RANGES} />}
    >
      {kind === "bar" ? (
        <BarChart {...common}>
          {grid}
          {xAxis}
          {yAxis}
          {tip}
          {series.map((s) => (
            <Bar key={s.key} dataKey={s.key} fill={s.color} stackId={stacked ? "a" : undefined} radius={[3, 3, 0, 0]} isAnimationActive={anim} animationDuration={700} />
          ))}
        </BarChart>
      ) : kind === "line" ? (
        <LineChart {...common}>
          {grid}
          {xAxis}
          {yAxis}
          {tip}
          {series.map((s, i) => (
            <Line
              key={s.key}
              dataKey={s.key}
              stroke={s.color}
              strokeWidth={2}
              strokeDasharray={i > 0 ? "5 4" : undefined}
              dot={false}
              isAnimationActive={anim} animationDuration={700}
            />
          ))}
        </LineChart>
      ) : (
        <AreaChart {...common}>
          {grid}
          {xAxis}
          {yAxis}
          {tip}
          {series.map((s) => (
            <Area
              key={s.key}
              dataKey={s.key}
              stroke={s.color}
              fill={s.color}
              fillOpacity={0.14}
              strokeWidth={2}
              stackId={stacked ? "a" : undefined}
              isAnimationActive={anim} animationDuration={700}
            />
          ))}
        </AreaChart>
      )}
    </ChartCard>
  );
}

export function BreakdownChart({
  title,
  description,
  unit,
  data,
  demo,
  summary,
}: {
  title: string;
  description?: string;
  unit?: string;
  data: { status: string; count: number }[];
  demo?: boolean;
  summary: string;
}) {
  const anim = useChartAnimation();
  const series: SeriesDef[] = [{ key: "count", label: unit ?? "Count", color: "var(--chart-1)" }];
  return (
    <ChartCard title={title} description={description} unit={unit} series={series} data={data} demo={demo} summary={summary}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 8 }}>
        <CartesianGrid stroke="var(--chart-grid)" horizontal={false} />
        <XAxis type="number" {...axisProps} />
        <YAxis type="category" dataKey="status" {...axisProps} axisLine={false} width={104} />
        <Tooltip content={(p) => <ChartTooltip {...p} series={series} />} cursor={{ fill: "var(--subtle)" }} />
        <Bar dataKey="count" fill="var(--chart-1)" radius={[0, 3, 3, 0]} barSize={18} isAnimationActive={anim} animationDuration={700} />
      </BarChart>
    </ChartCard>
  );
}
