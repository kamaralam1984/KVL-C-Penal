"use client";

import * as React from "react";
import { Tabs as T } from "radix-ui";
import { cn } from "@/lib/cn";

export const Tabs = T.Root;
export const TabsContent = T.Content;

export function TabsList({ className, ...props }: React.ComponentProps<typeof T.List>) {
  return <T.List className={cn("flex gap-1 border-b border-border", className)} {...props} />;
}

export function TabsTrigger({ className, ...props }: React.ComponentProps<typeof T.Trigger>) {
  return (
    <T.Trigger
      className={cn(
        "-mb-px border-b-2 border-transparent px-3 py-2 text-sm font-medium text-muted transition-colors",
        "hover:text-text data-[state=active]:border-primary data-[state=active]:text-text",
        className,
      )}
      {...props}
    />
  );
}

/** Compact radio-style switch for view modes and small option sets. */
export function Segmented<V extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: V;
  onChange: (v: V) => void;
  options: { value: V; label: string }[];
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-md bg-subtle p-0.5">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={cn(
              "rounded px-2.5 py-1 text-sm font-medium transition-all",
              active ? "bg-surface text-text shadow-sm" : "text-muted hover:text-text",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
