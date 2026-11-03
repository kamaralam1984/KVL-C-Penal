"use client";

import * as React from "react";
import { DropdownMenu as M, Popover as P } from "radix-ui";
import { cn } from "@/lib/cn";

export const Menu = M.Root;
export const MenuTrigger = M.Trigger;

export function MenuContent({
  children,
  align = "end",
  className,
}: {
  children: React.ReactNode;
  align?: "start" | "center" | "end";
  className?: string;
}) {
  return (
    <M.Portal>
      <M.Content
        align={align}
        sideOffset={6}
        className={cn(
          "z-[70] min-w-48 rounded-lg border border-border bg-elevated p-1 shadow-lg",
          "data-[state=open]:animate-[pop-in_var(--dur-fast)_var(--ease)]",
          className,
        )}
      >
        {children}
      </M.Content>
    </M.Portal>
  );
}

const itemCls =
  "flex w-full cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-text outline-none select-none " +
  "data-[highlighted]:bg-subtle data-[disabled]:pointer-events-none data-[disabled]:opacity-45";

export function MenuItem({
  icon: Icon,
  danger,
  className,
  children,
  ...props
}: React.ComponentProps<typeof M.Item> & { icon?: React.ComponentType<{ className?: string }>; danger?: boolean }) {
  // With asChild the Radix Slot requires exactly one child element, so the caller supplies the icon inside it.
  return (
    <M.Item className={cn(itemCls, danger && "text-danger", className)} {...props}>
      {props.asChild ? (
        children
      ) : (
        <>
          {Icon && <Icon className="h-4 w-4 text-muted" />}
          {children}
        </>
      )}
    </M.Item>
  );
}

export function MenuLabel({ children }: { children: React.ReactNode }) {
  return <M.Label className="px-2.5 py-1.5 text-xs font-medium tracking-wide text-faint uppercase">{children}</M.Label>;
}

export function MenuSeparator() {
  return <M.Separator className="my-1 h-px bg-border" />;
}

export const Popover = P.Root;
export const PopoverTrigger = P.Trigger;

export function PopoverContent({
  children,
  className,
  align = "end",
}: {
  children: React.ReactNode;
  className?: string;
  align?: "start" | "center" | "end";
}) {
  return (
    <P.Portal>
      <P.Content
        align={align}
        sideOffset={8}
        className={cn(
          "z-[70] w-[min(92vw,22rem)] rounded-lg border border-border bg-elevated shadow-lg",
          "data-[state=open]:animate-[pop-in_var(--dur-fast)_var(--ease)]",
          className,
        )}
      >
        {children}
      </P.Content>
    </P.Portal>
  );
}
