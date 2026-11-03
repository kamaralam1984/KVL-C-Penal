"use client";

import * as React from "react";
import { Dialog as D } from "radix-ui";
import { X } from "lucide-react";
import { Button } from "./button";
import { cn } from "@/lib/cn";

export const Dialog = D.Root;
export const DialogTrigger = D.Trigger;
export const DialogClose = D.Close;

const overlay =
  "fixed inset-0 z-[60] bg-black/45 data-[state=open]:animate-[fade-in_var(--dur-base)_var(--ease)] data-[state=closed]:opacity-0";

export function DialogContent({
  title,
  description,
  children,
  className,
  footer,
}: {
  title: string;
  description?: string;
  children?: React.ReactNode;
  className?: string;
  footer?: React.ReactNode;
}) {
  return (
    <D.Portal>
      <D.Overlay className={overlay} />
      <D.Content
        className={cn(
          "fixed top-1/2 left-1/2 z-[61] w-[min(92vw,30rem)] -translate-x-1/2 -translate-y-1/2",
          "rounded-xl border border-border bg-elevated shadow-lg focus:outline-none",
          "data-[state=open]:animate-[dialog-in_var(--dur-base)_var(--ease)]",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 px-5 pt-5">
          <div>
            <D.Title className="text-xl font-semibold tracking-tight text-text">{title}</D.Title>
            <D.Description className={cn("mt-1 text-sm text-muted", !description && "sr-only")}>
              {description ?? title}
            </D.Description>
          </div>
          <D.Close
            aria-label="Close"
            className="-mt-1 -mr-1 grid h-8 w-8 place-items-center rounded-md text-faint hover:bg-subtle hover:text-text"
          >
            <X className="h-4 w-4" />
          </D.Close>
        </div>
        {children && <div className="px-5 py-4">{children}</div>}
        {footer && <div className="flex justify-end gap-2 border-t border-border px-5 py-3">{footer}</div>}
      </D.Content>
    </D.Portal>
  );
}

/** Confirmation for destructive/irreversible actions. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  destructive,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description: string;
  confirmLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={title}
        description={description}
        footer={
          <>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              variant={destructive ? "danger" : "primary"}
              onClick={() => {
                onConfirm();
                onOpenChange(false);
              }}
            >
              {confirmLabel}
            </Button>
          </>
        }
      />
    </Dialog>
  );
}
