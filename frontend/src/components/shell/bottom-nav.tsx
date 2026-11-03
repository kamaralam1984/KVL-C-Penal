"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { Menu } from "lucide-react";
import { NAV_PERM, ROLES, findItem, type Role } from "@/config/nav";
import { useAuth } from "@/components/providers/auth-provider";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import { cn } from "@/lib/cn";

/** Native-app style tab bar for phones/tablets. Four primary destinations + "More" (opens the drawer). */
export function BottomNav({ role, onMore }: { role: Role; onMore: () => void }) {
  const pathname = usePathname();
  const cfg = ROLES[role];
  const { can } = useAuth();
  const tabs = cfg.mobileTabs.map((s) => findItem(role, s)!).filter((t) => t && (!NAV_PERM[role][t.slug] || can(NAV_PERM[role][t.slug])));

  const tab =
    "relative flex flex-1 flex-col items-center justify-center gap-0.5 pt-1.5 pb-1 text-[11px] font-medium transition-colors";

  return (
    <nav
      aria-label="Primary"
      className="glass-bar fixed inset-x-0 bottom-0 z-40 border-t border-border lg:hidden"
      style={{ paddingBottom: "var(--safe-bottom)" }}
    >
      <div className="flex h-[var(--bottom-nav-h)]">
        {tabs.map((t) => {
          const href = `${cfg.base}/${t.slug}`;
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link key={t.slug} href={href} aria-current={active ? "page" : undefined} className={cn(tab, active ? "text-primary" : "text-muted")}>
              {active && (
                <motion.span
                  layoutId="tab-indicator"
                  className="absolute top-0 h-[3px] w-10 rounded-b-full"
                  style={{ background: "linear-gradient(90deg, var(--silver), var(--gold), var(--gold-soft))" }}
                  transition={{ type: "spring", stiffness: 500, damping: 36 }}
                />
              )}
              <AnimatedIcon icon={t.icon} active={active} size={22} />
              <span>{t.label}</span>
            </Link>
          );
        })}
        <button type="button" onClick={onMore} className={cn(tab, "text-muted")} aria-label="More navigation">
          <AnimatedIcon icon={Menu} size={22} />
          <span>More</span>
        </button>
      </div>
    </nav>
  );
}
