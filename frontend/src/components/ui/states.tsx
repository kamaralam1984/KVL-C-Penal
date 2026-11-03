import { AlertTriangle, Clock, Inbox, Lock, RotateCw } from "lucide-react";
import { Button } from "./button";
import { cn } from "@/lib/cn";

function StateShell({
  icon: Icon,
  title,
  description,
  action,
  tone = "text-faint",
  className,
  role,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  tone?: string;
  className?: string;
  role?: "alert" | "status";
}) {
  return (
    <div role={role} className={cn("flex flex-col items-center px-6 py-10 text-center", className)}>
      <div className="mb-3 grid h-11 w-11 place-items-center rounded-full bg-subtle">
        <Icon className={cn("h-5 w-5", tone)} />
      </div>
      <h3 className="text-lg font-semibold text-text">{title}</h3>
      {description && <p className="mt-1 max-w-sm text-sm text-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function EmptyState(p: { title: string; description?: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return <StateShell icon={Inbox} {...p} />;
}

export function ErrorState({
  title = "Something went wrong",
  description = "The data could not be loaded. Check your connection and try again.",
  onRetry,
  className,
}: {
  title?: string;
  description?: React.ReactNode;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <StateShell
      role="alert"
      icon={AlertTriangle}
      tone="text-danger"
      title={title}
      description={description}
      className={className}
      action={
        onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RotateCw className="h-3.5 w-3.5" /> Retry
          </Button>
        )
      }
    />
  );
}

export function PermissionDenied({
  description = "Your role does not have access to this area. Ask a platform owner if you believe this is a mistake.",
  action,
}: {
  description?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return <StateShell role="alert" icon={Lock} tone="text-warning" title="Permission denied" description={description} action={action} />;
}

/** Honest placeholder for routes whose backing feature is not built yet. */
export function NotYetAvailable({
  feature,
  phase,
  description,
  action,
}: {
  feature: string;
  phase: number;
  description?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <StateShell
      role="status"
      icon={Clock}
      tone="text-info"
      title={`${feature} is not available yet`}
      description={
        description ?? (
          <>
            This module is planned for <strong className="font-semibold text-text">Phase {phase}</strong> of the build. The
            route and navigation are in place; no functionality or data exists yet.
          </>
        )
      }
      action={action}
    />
  );
}
