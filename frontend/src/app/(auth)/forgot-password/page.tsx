import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { AuthShell } from "@/components/auth/auth-shell";
import { ForgotForm } from "@/components/auth/forms";

export const metadata: Metadata = { title: "Forgot password" };

export default function Page() {
  return (
    <AuthShell title="Forgot password" subtitle="We will email you a code to reset it." footer={<Link href="/login" className="font-medium text-primary hover:underline">Back to sign in</Link>}>
      <Suspense>
        <ForgotForm />
      </Suspense>
    </AuthShell>
  );
}
