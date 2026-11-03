"use client";

import { Tooltip as T } from "radix-ui";

export const TooltipProvider = T.Provider;

export function Tooltip({
  content,
  children,
  side = "top",
}: {
  content: React.ReactNode;
  children: React.ReactNode;
  side?: "top" | "right" | "bottom" | "left";
}) {
  return (
    <T.Root>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          side={side}
          sideOffset={6}
          className="z-[70] max-w-xs rounded-md bg-text px-2 py-1 text-xs font-medium text-bg shadow-md"
        >
          {content}
          <T.Arrow className="fill-text" />
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
