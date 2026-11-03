"use client";

import { Suspense } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { useAuth } from "@/components/providers/auth-provider";
import type { Role } from "@/config/nav";

const AREA: Record<string, Role> = { super_admin: "admin", support: "admin", hosting_admin: "hosting", customer: "customer", reseller: "reseller" };

/** /account is shared by every role; it borrows the shell of the area the signed-in role belongs to. */
export default function AccountLayout({ children }: { children: React.ReactNode }) {
  const { me } = useAuth();
  const role = (me?.role && AREA[me.role]) || "customer";
  return (
    <Suspense fallback={null}>
      <AppShell role={role}>{children}</AppShell>
    </Suspense>
  );
}
