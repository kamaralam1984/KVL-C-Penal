import { cn } from "@/lib/cn";

/** Animated gold + silver line. Decorative only; hidden from assistive tech. */
export function AccentLine({ className, vertical }: { className?: string; vertical?: boolean }) {
  return <div aria-hidden className={cn(vertical ? "accent-line-v" : "accent-line", className)} />;
}
