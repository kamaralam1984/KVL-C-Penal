import type { Quota } from "./api";
import { Card } from "@/components/ui/card";

export function QuotaMeter({ quota }: { quota?: Quota }) {
  if (!quota) return null;
  if (!quota.configured)
    return (
      <Card className="p-3 text-sm text-muted" role="status">
        <strong className="text-text">Storage quota:</strong> not configured for this account. Usage is not limited or measured here.
      </Card>
    );
  const pct = Math.min(100, quota.percent ?? 0);
  const tone = pct >= 90 ? "bg-danger" : pct >= 75 ? "bg-warning" : "bg-primary";
  return (
    <Card className="p-3">
      <div className="mb-1.5 flex justify-between text-sm">
        <span className="font-medium text-text">Storage</span>
        <span className="tnum text-muted">
          {quota.used_mb} / {quota.limit_mb} MB ({pct.toFixed(0)}%)
        </span>
      </div>
      <div role="progressbar" aria-label="Storage used" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} className="h-2 overflow-hidden rounded-full bg-subtle">
        <div className={`h-full rounded-full transition-all ${tone}`} style={{ width: `${pct}%` }} />
      </div>
      {pct >= 90 && <p className="mt-1.5 text-xs text-danger">Almost full. Uploads will be refused at 100%.</p>}
    </Card>
  );
}
