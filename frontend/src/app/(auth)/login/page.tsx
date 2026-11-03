import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { AuthShell } from "@/components/auth/auth-shell";
import { LoginForm } from "@/components/auth/forms";

export const metadata: Metadata = { title: "Sign in" };

export default function Page() {
  return (
    <AuthShell title="Sign in" subtitle="Welcome back. Sign in to your KVL account." footer={<>New here? <Link href="/register" className="font-medium text-primary hover:underline">Create an account</Link></>}>
      <Suspense>
        <LoginForm />
      </Suspense>
    </AuthShell>
  );
}
