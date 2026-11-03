"use client";

import * as React from "react";
import { usePathname } from "next/navigation";
import { MotionConfig, motion } from "framer-motion";
import { Search } from "lucide-react";
import { Sidebar } from "./sidebar";
import { BottomNav } from "./bottom-nav";
import { AccentLine } from "@/components/ui/accent-line";
import { Breadcrumbs } from "./breadcrumbs";
import { CommandSearch } from "./command-search";
import { ThemeSelector } from "./theme-selector";
import { NotificationsMenu, ProfileMenu } from "./topbar-menus";
import { IconButton } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { useLocalFlag } from "@/hooks/use-local-flag";
import { homeFor, useRequireAuth } from "@/components/providers/auth-provider";
import { PermissionDenied } from "@/components/ui/states";
import { CardSkeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import Link from "next/link";
import { ROLES, type Role } from "@/config/nav";
import { cn } from "@/lib/cn";

export function AppShell({ role, children }: { role: Role; children: React.ReactNode }) {
  const pathname = usePathname();
  const { me, loading, allowed } = useRequireAuth([role]);
  const [collapsed, setCollapsed] = useLocalFlag("kvl.sidebar.collapsed", false);
  const [mobileOpen, setMobileOpen] = React.useState(false);
  const [searchOpen, setSearchOpen] = React.useState(false);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    // reducedMotion="user" makes every Framer animation honour prefers-reduced-motion.
    <MotionConfig reducedMotion="user">
      <a
        href="#main-content"
        className="sr-only z-[100] rounded-md bg-primary px-4 py-2 text-primary-fg focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to main content
      </a>

      <aside
        aria-label="Sidebar"
        className={cn(
          "fixed inset-y-0 left-0 z-40 hidden border-r border-border transition-[width] duration-200 lg:block",
          collapsed ? "w-[var(--sidebar-w-collapsed)]" : "w-[var(--sidebar-w)]",
        )}
      >
        <Sidebar role={role} collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" title="Navigation" description={`${ROLES[role].label} menu`} hideHeader className="w-[min(86vw,18rem)]">
          <Sidebar role={role} onNavigate={() => setMobileOpen(false)} />
        </SheetContent>
      </Sheet>

      <div className={cn("transition-[padding] duration-200", collapsed ? "lg:pl-[var(--sidebar-w-collapsed)]" : "lg:pl-[var(--sidebar-w)]")}>
        <header className="glass-bar sticky top-0 z-30 flex h-[var(--topbar-h)] items-center gap-2 border-b border-border px-3 pt-[var(--safe-top)] sm:px-5">
          <div className="absolute inset-x-0 bottom-0"><AccentLine /></div>
                    <Breadcrumbs role={role} />

          <div className="ml-auto flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setSearchOpen(true)}
              aria-label="Search (Ctrl K)"
              className="hidden h-9 w-56 items-center gap-2 rounded-md border border-border-strong bg-surface px-3 text-sm text-faint hover:bg-subtle md:flex"
            >
              <Search className="h-4 w-4" aria-hidden />
              Search…
              <kbd className="ml-auto rounded border border-border px-1.5 text-[11px]">Ctrl K</kbd>
            </button>
            <IconButton label="Search" className="md:hidden" onClick={() => setSearchOpen(true)}>
              <Search className="h-[18px] w-[18px]" />
            </IconButton>
            <ThemeSelector />
            <NotificationsMenu />
            <ProfileMenu role={role} />
          </div>
        </header>

        <main id="main-content" tabIndex={-1} className="app-main mx-auto w-full max-w-[var(--content-max)] px-4 py-5 focus:outline-none sm:px-6 sm:py-6 lg:px-8">
          <motion.div
            key={pathname}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, ease: [0.2, 0, 0, 1] }}
          >
            {loading || !me ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <CardSkeleton />
                <CardSkeleton />
              </div>
            ) : allowed ? (
              children
            ) : (
              <PermissionDenied
                action={
                  <Button asChild variant="outline" size="sm">
                    <Link href={homeFor(me.role)}>Go to your area</Link>
                  </Button>
                }
              />
            )}
          </motion.div>
        </main>
      </div>

      <BottomNav role={role} onMore={() => setMobileOpen(true)} />
      <CommandSearch role={role} open={searchOpen} onOpenChange={setSearchOpen} />
    </MotionConfig>
  );
}
