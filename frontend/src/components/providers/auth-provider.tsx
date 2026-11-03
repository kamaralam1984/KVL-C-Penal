"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ApiError, get, post } from "@/lib/http";

export interface Me {
  user: { id: string; email: string; name: string; totp_enabled: boolean; email_verified: boolean };
  active_membership_id: string | null;
  role: string | null;
  scope: "platform" | "tenant" | "reseller" | null;
  tenant_id: string | null;
  tenant_status: string | null;
  permissions: string[];
  memberships: { id: string; role: string; role_label: string; tenant_id: string | null }[];
}

interface AuthCtx {
  me: Me | null;
  loading: boolean;
  can: (perm: string) => boolean;
  reload: () => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = React.createContext<AuthCtx | null>(null);

export function useAuth() {
  const c = React.useContext(Ctx);
  if (!c) throw new Error("useAuth must be used inside <AuthProvider>");
  return c;
}

/** The area (route prefix) a role lands in. */
export function homeFor(role: string | null): string {
  switch (role) {
    case "super_admin":
    case "support":
      return "/admin/dashboard";
    case "hosting_admin":
      return "/hosting/dashboard";
    case "reseller":
      return "/reseller/dashboard";
    case "customer":
      return "/customer/dashboard";
    default:
      return "/login";
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = React.useState<Me | null>(null);
  const [loading, setLoading] = React.useState(true);

  const reload = React.useCallback(async () => {
    try {
      setMe(await get<Me>("/auth/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [reload]);

  const logout = React.useCallback(async () => {
    try {
      await post("/auth/logout");
    } finally {
      setMe(null);
    }
  }, []);

  const perms = React.useMemo(() => new Set(me?.permissions ?? []), [me]);
  const value = React.useMemo<AuthCtx>(() => ({ me, loading, can: (p) => perms.has(p), reload, logout }), [me, loading, perms, reload, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Client-side gate: redirects anonymous users to /login. Navigation hiding is UX only; the API enforces access. */
export function useRequireAuth(areas: string[]) {
  const { me, loading } = useAuth();
  const router = useRouter();
  React.useEffect(() => {
    if (loading) return;
    if (!me) router.replace(`/login?next=${encodeURIComponent(window.location.pathname)}`);
  }, [loading, me, router]);
  const allowed = !!me && areas.some((a) => (AREA_ROLES[a] ?? []).includes(me.role ?? ""));
  return { me, loading, allowed };
}

export const AREA_ROLES: Record<string, string[]> = {
  admin: ["super_admin", "support"],
  hosting: ["super_admin", "hosting_admin"],
  customer: ["customer"],
  reseller: ["reseller"],
};
