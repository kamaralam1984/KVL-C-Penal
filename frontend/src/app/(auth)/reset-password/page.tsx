import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { AuthShell } from "@/components/auth/auth-shell";
import { ResetForm } from "@/components/auth/forms";

export const metadata: Metadata = { title: "Reset password" };

export default function Page() {
  return (
    <AuthShell title="Reset password" subtitle="Enter the code and choose a new password." footer={<Link href="/login" className="font-medium text-primary hover:underline">Back to sign in</Link>}>
      <Suspense>
        <ResetForm />
      </Suspense>
    </AuthShell>
  );
}
