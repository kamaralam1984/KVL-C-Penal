import { Badge, type Tone } from "@/components/ui/badge";
import type { Availability } from "./types";

const META: Record<Availability, { tone: Tone; label: string; hint: string }> = {
  connected: { tone: "success", label: "Connected", hint: "Heartbeat received within the last 90 seconds." },
  stale: { tone: "warning", label: "Stale", hint: "No heartbeat for 90 seconds to 5 minutes." },
  disconnected: { tone: "danger", label: "Disconnected", hint: "No heartbeat for more than 5 minutes." },
  unknown: { tone: "neutral", label: "Unknown", hint: "No heartbeat has ever been received from this server." },
  disabled: { tone: "neutral", label: "Disabled", hint: "This server is disabled; its agent credentials are revoked." },
};

export function AvailabilityBadge({ value }: { value: Availability | string }) {
  const m = META[value as Availability] ?? META.unknown;
  return (
    <span title={m.hint}>
      <Badge tone={m.tone} dot>
        {m.label}
      </Badge>
    </span>
  );
}

export const availabilityHint = (v: string) => (META[v as Availability] ?? META.unknown).hint;
