"use client";

import * as React from "react";
import { animate, motion, useReducedMotion } from "framer-motion";
import { cn } from "@/lib/cn";

export type Level = "ok" | "warning" | "critical" | "none";

export function levelOf(v: number | null | undefined, warn = 75, crit = 90): Level {
  if (v == null) return "none";
  return v >= crit ? "critical" : v >= warn ? "warning" : "ok";
}

const levelColor: Record<Level, string> = {
  ok: "var(--primary)",
  warning: "var(--warning)",
  critical: "var(--danger)",
  none: "var(--border-strong)",
};

/** Number that counts up/down to its target. Honours reduced motion by jumping straight to the value. */
export function AnimatedNumber({ value, decimals = 0, className }: { value: number; decimals?: number; className?: string }) {
  const reduce = useReducedMotion();
  const ref = React.useRef<HTMLSpanElement>(null);
  const prev = React.useRef(0);
  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const from = prev.current;
    prev.current = value;
    if (reduce) {
      el.textContent = value.toFixed(decimals);
      return;
    }
    const c = animate(from, value, { duration: 0.8, ease: "easeOut", onUpdate: (v) => (el.textContent = v.toFixed(decimals)) });
    return () => c.stop();
  }, [value, decimals, reduce]);
  return <span ref={ref} className={cn("tnum", className)}>{value.toFixed(decimals)}</span>;
}

/**
 * Round progress gauge. The arc sweeps from 0 to the value, with a gold-to-silver sheen on the track end.
 * A missing value renders an empty dashed ring with an em dash: unknown is never drawn as zero.
 */
export function RingGauge({
  value,
  label,
  caption,
  size = 132,
  warn,
  crit,
  unit = "%",
  decimals = 0,
}: {
  value: number | null | undefined;
  label: string;
  caption?: string;
  size?: number;
  warn?: number;
  crit?: number;
  unit?: string;
  decimals?: number;
}) {
  const reduce = useReducedMotion();
  const stroke = Math.max(8, Math.round(size / 11));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const known = value != null && Number.isFinite(value);
  const pct = known ? Math.max(0, Math.min(100, value)) : 0;
  const level = levelOf(known ? value : null, warn, crit);
  const id = React.useId();
  return (
    <figure className="flex flex-col items-center gap-2" aria-label={`${label}: ${known ? `${value.toFixed(decimals)}${unit}` : "no data"}`}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
          <defs>
            <linearGradient id={`g${id}`} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor={levelColor[level]} />
              <stop offset="100%" stopColor={level === "ok" ? "var(--gold)" : levelColor[level]} />
            </linearGradient>
          </defs>
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--subtle)" strokeWidth={stroke} strokeDasharray={known ? undefined : "3 6"} />
          {known && (
            <motion.circle
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke={`url(#g${id})`}
              strokeWidth={stroke}
              strokeLinecap="round"
              strokeDasharray={c}
              initial={{ strokeDashoffset: reduce ? c * (1 - pct / 100) : c }}
              animate={{ strokeDashoffset: c * (1 - pct / 100) }}
              transition={reduce ? { duration: 0 } : { type: "spring", stiffness: 60, damping: 18 }}
            />
          )}
        </svg>
        <div className="absolute inset-0 grid place-items-center text-center">
          <div>
            <p className="tnum text-2xl font-semibold leading-none text-text" style={{ fontSize: size / 5 }}>
              {known ? <AnimatedNumber value={value} decimals={decimals} /> : "—"}
              {known && <span className="ml-0.5 font-medium text-muted" style={{ fontSize: size / 9 }}>{unit}</span>}
            </p>
          </div>
        </div>
      </div>
      <figcaption className="text-center">
        <span className="block text-sm font-medium text-text">{label}</span>
        {caption && <span className="tnum block text-xs text-muted">{caption}</span>}
      </figcaption>
    </figure>
  );
}

/** Horizontal meter whose fill animates to its value. `value` is a percentage of the bar. */
export function BarMeter({
  value,
  label,
  right,
  warn,
  crit,
  color,
  className,
}: {
  value: number | null | undefined;
  label?: React.ReactNode;
  right?: React.ReactNode;
  warn?: number;
  crit?: number;
  color?: string;
  className?: string;
}) {
  const reduce = useReducedMotion();
  const known = value != null && Number.isFinite(value);
  const pct = known ? Math.max(0, Math.min(100, value)) : 0;
  const fill = color ?? (warn !== undefined || crit !== undefined ? levelColor[levelOf(known ? value : null, warn, crit)] : "var(--primary)");
  return (
    <div className={cn("space-y-1", className)}>
      {(label || right) && (
        <div className="flex items-baseline justify-between gap-2 text-xs">
          <span className="truncate text-muted">{label}</span>
          <span className="tnum shrink-0 font-medium text-text">{right ?? (known ? `${value.toFixed(0)}%` : "—")}</span>
        </div>
      )}
      <div className="h-2 overflow-hidden rounded-full bg-subtle" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={known ? Math.round(pct) : undefined} aria-label={typeof label === "string" ? label : undefined}>
        <motion.div
          className="h-full rounded-full"
          style={{ background: fill }}
          initial={{ width: reduce ? `${pct}%` : 0 }}
          animate={{ width: `${pct}%` }}
          transition={reduce ? { duration: 0 } : { type: "spring", stiffness: 90, damping: 20 }}
        />
      </div>
    </div>
  );
}
