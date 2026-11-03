import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { AuthShell } from "@/components/auth/auth-shell";
import { RegisterForm } from "@/components/auth/forms";

export const metadata: Metadata = { title: "Create account" };

export default function Page() {
  return (
    <AuthShell title="Create account" subtitle="Start your hosting account in under a minute." footer={<>Already registered? <Link href="/login" className="font-medium text-primary hover:underline">Sign in</Link></>}>
      <Suspense>
        <RegisterForm />
      </Suspense>
    </AuthShell>
  );
}
