import { Suspense } from "react";
import { AppShell } from "@/components/shell/app-shell";

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={null}>
      <AppShell role="reseller">{children}</AppShell>
    </Suspense>
  );
}
