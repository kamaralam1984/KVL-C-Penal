import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { Card } from "./card";
import { AnimatedIcon } from "./animated-icon";
import { AccentLine } from "./accent-line";
import { DemoBadge } from "./badge";
import { cn } from "@/lib/cn";

export function KpiCard({
  label,
  value,
  unit,
  icon: Icon,
  delta,
  deltaGoodWhen = "up",
  hint,
  demo,
}: {
  label: string;
  value: string;
  unit?: string;
  icon?: React.ComponentType<{ className?: string }>;
  /** Percentage change vs previous period. */
  delta?: number;
  deltaGoodWhen?: "up" | "down";
  hint?: string;
  demo?: boolean;
}) {
  const good = delta === undefined || delta === 0 ? null : (delta > 0) === (deltaGoodWhen === "up");
  const DeltaIcon = delta === undefined || delta === 0 ? Minus : delta > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <Card className="relative overflow-hidden p-4">
      <div className="absolute inset-x-0 top-0 opacity-80"><AccentLine className="!h-[2px]" /></div>
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium text-muted">{label}</p>
        {Icon && (
          <span className="grid h-8 w-8 place-items-center rounded-md bg-primary-soft text-primary">
            <AnimatedIcon icon={Icon} size={16} idle="float" />
          </span>
        )}
      </div>
      <p className="tnum mt-2 text-3xl font-semibold tracking-tight text-text">
        {value}
        {unit && <span className="ml-1 text-lg font-medium text-muted">{unit}</span>}
      </p>
      <div className="mt-2 flex items-center gap-2 text-xs">
        {delta !== undefined && (
          <span
            className={cn(
              "tnum inline-flex items-center gap-0.5 font-medium",
              good === null ? "text-muted" : good ? "text-success" : "text-danger",
            )}
          >
            <DeltaIcon className="h-3.5 w-3.5" aria-hidden />
            {Math.abs(delta)}%
            <span className="sr-only"> {delta > 0 ? "increase" : delta < 0 ? "decrease" : "no change"} versus previous period</span>
          </span>
        )}
        {hint && <span className="text-muted">{hint}</span>}
        {demo && <DemoBadge className="ml-auto" />}
      </div>
    </Card>
  );
}
