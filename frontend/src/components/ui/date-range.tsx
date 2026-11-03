"use client";

import { Segmented } from "./tabs";

export type RangeKey = "24h" | "7d" | "14d" | "30d" | "90d";

export const RANGE_OPTIONS: { value: RangeKey; label: string }[] = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "90d", label: "90d" },
];

export function DateRangeSelector({
  value,
  onChange,
  options = RANGE_OPTIONS,
}: {
  value: RangeKey;
  onChange: (v: RangeKey) => void;
  options?: { value: RangeKey; label: string }[];
}) {
  return <Segmented label="Time range" value={value} onChange={onChange} options={options} />;
}
