"use client";

import Link from "next/link";
import { Bell, Check, LogOut, Repeat, UserCircle } from "lucide-react";
import { IconButton } from "@/components/ui/button";
import { LiveBadge } from "@/components/resource/helpers";
import { useApi } from "@/hooks/use-api";
import { useRouter } from "next/navigation";
import { useToast } from "@/components/ui/toast";
import { AREA_ROLES, homeFor, useAuth } from "@/components/providers/auth-provider";
import { post } from "@/lib/http";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger, Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { ROLES, ROLE_ORDER, type Role } from "@/config/nav";
import { cn } from "@/lib/cn";

const severityDot = { info: "bg-info", warning: "bg-warning", critical: "bg-danger" };

interface FeedItem { id: string; title: string; body: string; severity: "info" | "warning" | "critical"; read: boolean; require_ack: boolean; acknowledged: boolean; created_at: string }

export function NotificationsMenu() {
  const { data, error, loading, reload } = useApi<{ unread: number; pending_ack: number; items: FeedItem[] }>("/announcements/feed", undefined, { refreshMs: 60000 });
  const unread = data?.unread ?? 0;
  const markRead = async (n: FeedItem) => {
    if (n.read) return;
    try {
      await post(`/announcements/${n.id}/read`);
      reload();
    } catch {
      /* the next poll reconciles */
    }
  };
  return (
    <Popover>
      <PopoverTrigger asChild>
        <IconButton label={unread ? `Notifications (${unread} unread)` : "Notifications"} className="relative">
          <Bell className="h-[18px] w-[18px]" />
          {unread > 0 && <span className="absolute top-2 right-2 h-2 w-2 rounded-full bg-accent ring-2 ring-surface" aria-hidden />}
        </IconButton>
      </PopoverTrigger>
      <PopoverContent className="p-0">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-base font-semibold text-text">Notifications</h2>
          <LiveBadge />
        </div>
        <ul className="max-h-80 divide-y divide-border overflow-y-auto">
          {loading && <li className="px-4 py-3 text-sm text-muted">Loading…</li>}
          {error && <li className="px-4 py-3 text-sm text-danger">Could not load notifications.</li>}
          {data?.items.length === 0 && <li className="px-4 py-6 text-center text-sm text-muted">You are all caught up.</li>}
          {data?.items.map((n) => (
            <li key={n.id}>
              <button type="button" onClick={() => markRead(n)} className="flex min-h-11 w-full gap-3 px-4 py-3 text-left hover:bg-subtle">
                <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", n.read ? "bg-border-strong" : severityDot[n.severity])} aria-hidden />
                <span className="min-w-0">
                  <span className={cn("block text-sm text-text", !n.read && "font-semibold")}>{n.title}</span>
                  <span className="line-clamp-2 block text-xs text-muted">{n.body}</span>
                  {n.require_ack && !n.acknowledged && <span className="text-xs text-warning">Acknowledgement requested</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <p className="border-t border-border px-4 py-2.5 text-xs text-muted">Platform announcements addressed to you.</p>
      </PopoverContent>
    </Popover>
  );
}

export function ProfileMenu({ role }: { role: Role }) {
  const toast = useToast();
  const router = useRouter();
  const { me, logout, reload } = useAuth();
  const cfg = ROLES[role];
  const initials = (me?.user.name ?? "?").split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  const areas = ROLE_ORDER.filter((r) => me && AREA_ROLES[r].includes(me.role ?? ""));
  return (
    <Menu>
      <MenuTrigger asChild>
        <button
          aria-label="Account menu"
          className="flex h-9 items-center gap-2 rounded-full border border-border bg-surface pr-3 pl-1 hover:bg-subtle"
        >
          <span className="grid h-7 w-7 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-fg">{initials}</span>
          <span className="hidden text-sm font-medium text-text sm:block">{me?.user.name ?? "Account"}</span>
        </button>
      </MenuTrigger>
      <MenuContent className="w-60">
        <div className="px-2.5 py-2">
          <p className="text-sm font-medium text-text">{me?.user.name}</p>
          <p className="truncate text-xs text-muted">{me?.user.email}</p>
          <p className="text-xs text-faint">{cfg.label}</p>
        </div>
        <MenuSeparator />
        {areas.length > 1 && (
          <>
            <MenuLabel>Switch area</MenuLabel>
            {areas.map((r) => (
              <MenuItem key={r} asChild>
                <Link href={`${ROLES[r].base}/dashboard`}>
                  {r === role ? <Check className="h-4 w-4 text-primary" /> : <Repeat className="h-4 w-4 text-muted" />}
                  {ROLES[r].label}
                </Link>
              </MenuItem>
            ))}
            <MenuSeparator />
          </>
        )}
        {me && me.memberships.length > 1 && (
          <>
            <MenuLabel>Switch account</MenuLabel>
            {me.memberships.map((m) => (
              <MenuItem
                key={m.id}
                icon={m.id === me.active_membership_id ? Check : Repeat}
                onSelect={async () => {
                  try {
                    await post("/auth/switch-membership", { membership_id: m.id });
                    await reload();
                    router.push(homeFor(m.role));
                  } catch {
                    toast({ title: "Could not switch account", tone: "danger" });
                  }
                }}
              >
                {m.role_label}
              </MenuItem>
            ))}
            <MenuSeparator />
          </>
        )}
        <MenuItem asChild>
          <Link href="/account">
            <UserCircle className="h-4 w-4 text-muted" />
            Profile &amp; security
          </Link>
        </MenuItem>
        <MenuItem
          icon={LogOut}
          onSelect={async () => {
            await logout();
            router.replace("/login");
          }}
        >
          Sign out
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}
