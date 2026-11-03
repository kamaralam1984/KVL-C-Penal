"use client";

import * as React from "react";
import { Dialog as D } from "radix-ui";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

export const Sheet = D.Root;
export const SheetTrigger = D.Trigger;
export const SheetClose = D.Close;

/** Edge drawer built on the dialog primitive (focus trap, Esc, scroll lock included). */
export function SheetContent({
  title,
  description,
  side = "right",
  children,
  className,
  hideHeader,
}: {
  title: string;
  description?: string;
  side?: "left" | "right";
  children: React.ReactNode;
  className?: string;
  hideHeader?: boolean;
}) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-[60] bg-black/45 data-[state=open]:animate-[fade-in_var(--dur-base)_var(--ease)]" />
      <D.Content
        className={cn(
          "fixed inset-y-0 z-[61] flex w-[min(92vw,26rem)] flex-col border-border bg-elevated shadow-lg focus:outline-none",
          side === "right"
            ? "right-0 border-l data-[state=open]:animate-[slide-in-right_var(--dur-slow)_var(--ease)]"
            : "left-0 border-r data-[state=open]:animate-[slide-in-left_var(--dur-slow)_var(--ease)]",
          className,
        )}
      >
        <div className={cn("flex items-start justify-between gap-3 border-b border-border px-5 py-4", hideHeader && "sr-only")}>
          <div>
            <D.Title className="text-lg font-semibold text-text">{title}</D.Title>
            <D.Description className={cn("mt-0.5 text-sm text-muted", !description && "sr-only")}>
              {description ?? title}
            </D.Description>
          </div>
          <D.Close aria-label="Close" className="grid h-8 w-8 place-items-center rounded-md text-faint hover:bg-subtle hover:text-text">
            <X className="h-4 w-4" />
          </D.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </D.Content>
    </D.Portal>
  );
}
