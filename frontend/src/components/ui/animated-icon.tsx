"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/cn";

type Idle = "none" | "float" | "ring" | "pulse" | "spin";
const idleCls: Record<Idle, string> = { none: "", float: "icon-float", ring: "icon-ring", pulse: "icon-pulse", spin: "icon-spin-slow" };

/**
 * Wraps any Lucide icon with tactile motion: springy hover/tap, plus an optional idle loop.
 * Honors reduced motion through the global MotionConfig and the CSS media rule.
 */
export function AnimatedIcon({
  icon: Icon,
  className,
  idle = "none",
  active,
  size = 18,
}: {
  icon: React.ComponentType<{ className?: string; size?: number; strokeWidth?: number }>;
  className?: string;
  idle?: Idle;
  active?: boolean;
  size?: number;
}) {
  return (
    <motion.span
      aria-hidden
      className={cn("inline-grid shrink-0 place-items-center", className)}
      whileHover={{ scale: 1.18, rotate: -6 }}
      animate={active ? { scale: [1, 1.15, 1] } : { scale: 1 }}
      transition={{ type: "spring", stiffness: 420, damping: 18 }}
    >
      <Icon size={size} strokeWidth={active ? 2.3 : 1.9} className={idleCls[idle]} />
    </motion.span>
  );
}
