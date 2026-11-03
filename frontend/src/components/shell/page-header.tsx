import { DemoBadge } from "@/components/ui/badge";
import { AccentLine } from "@/components/ui/accent-line";

export function PageHeader({
  title,
  description,
  actions,
  demo,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  demo?: boolean;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2.5">
          <h1 className="text-2xl font-semibold tracking-tight text-text">{title}</h1>
          {demo && <DemoBadge />}
        </div>
        <div className="mt-2 w-16"><AccentLine /></div>
        {description && <p className="mt-2 max-w-2xl text-base text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
