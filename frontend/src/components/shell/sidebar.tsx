"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronsLeft, ChevronsRight } from "lucide-react";
import { NAV_PERM, ROLES, type Role } from "@/config/nav";
import { useAuth } from "@/components/providers/auth-provider";
import { Brand } from "./brand";
import { Tooltip } from "@/components/ui/tooltip";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import { AccentLine } from "@/components/ui/accent-line";
import { cn } from "@/lib/cn";

export function Sidebar({
  role,
  collapsed = false,
  onToggle,
  onNavigate,
}: {
  role: Role;
  collapsed?: boolean;
  onToggle?: () => void;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const cfg = ROLES[role];
  const { can } = useAuth();
  const sections = cfg.sections
    .map((sec) => ({ ...sec, items: sec.items.filter((it) => !NAV_PERM[role][it.slug] || can(NAV_PERM[role][it.slug])) }))
    .filter((sec) => sec.items.length);

  return (
    <div className="flex h-full flex-col bg-surface">
      <div className={cn("relative flex h-[var(--topbar-h)] shrink-0 items-center border-b border-border", collapsed ? "justify-center" : "px-4")}>
        <Link href={`${cfg.base}/dashboard`} aria-label="KVL Hosting Panel home" onClick={onNavigate} className="rounded-md">
          <Brand collapsed={collapsed} />
        </Link>
      </div>

      <nav aria-label={`${cfg.label} navigation`} className="min-h-0 flex-1 overflow-y-auto px-2.5 py-3">
        {sections.map((section, i) => (
          <div key={section.title ?? i} className={cn(i > 0 && "mt-4")}>
            {section.title && !collapsed && (
              <p className="px-2.5 pb-1 text-[11px] font-semibold tracking-wider text-faint uppercase">{section.title}</p>
            )}
            {section.title && collapsed && i > 0 && <div className="mx-2 mb-2 h-px bg-border" />}
            <ul className="space-y-0.5">
              {section.items.map((it) => {
                const href = `${cfg.base}/${it.slug}`;
                const active = pathname === href || pathname.startsWith(`${href}/`);
                const link = (
                  <Link
                    href={href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    aria-label={collapsed ? it.label : undefined}
                    className={cn(
                      "group relative flex items-center gap-3 rounded-md px-2.5 py-2 text-base font-medium transition-colors",
                      collapsed && "justify-center px-0",
                      active ? "bg-primary-soft text-primary" : "text-muted hover:bg-subtle hover:text-text",
                    )}
                  >
                    {active && (
                      <span className="absolute top-1.5 bottom-1.5 left-0 w-0.5 overflow-hidden rounded-full" aria-hidden>
                        <AccentLine vertical />
                      </span>
                    )}
                    <AnimatedIcon icon={it.icon} active={active} />
                    {!collapsed && <span className="truncate">{it.label}</span>}
                  </Link>
                );
                return (
                  <li key={it.slug}>
                    {collapsed ? (
                      <Tooltip content={it.label} side="right">
                        {link}
                      </Tooltip>
                    ) : (
                      link
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      {onToggle && (
        <div className="hidden shrink-0 border-t border-border p-2.5 lg:block">
          <button
            type="button"
            onClick={onToggle}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-pressed={collapsed}
            className={cn(
              "flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-sm font-medium text-muted hover:bg-subtle hover:text-text",
              collapsed && "justify-center px-0",
            )}
          >
            {collapsed ? <ChevronsRight className="h-4 w-4" /> : <ChevronsLeft className="h-4 w-4" />}
            {!collapsed && "Collapse"}
          </button>
        </div>
      )}
    </div>
  );
}
