import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { AuthShell } from "@/components/auth/auth-shell";
import { VerifyEmailForm } from "@/components/auth/forms";

export const metadata: Metadata = { title: "Verify your email" };

export default function Page() {
  return (
    <AuthShell title="Verify your email" subtitle="Enter the 6-digit code we sent to confirm your address." footer={<Link href="/login" className="font-medium text-primary hover:underline">Back to sign in</Link>}>
      <Suspense>
        <VerifyEmailForm />
      </Suspense>
    </AuthShell>
  );
}
