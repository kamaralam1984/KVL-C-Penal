import type { Metadata } from "next";
import { Suspense } from "react";
import { AuthShell } from "@/components/auth/auth-shell";
import { AcceptInviteForm } from "@/components/auth/forms";

export const metadata: Metadata = { title: "Accept invitation" };

export default function Page() {
  return (
    <AuthShell title="Accept invitation" subtitle="Set up your account to join the team." footer={null}>
      <Suspense>
        <AcceptInviteForm />
      </Suspense>
    </AuthShell>
  );
}
