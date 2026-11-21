"use client";

import * as React from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { useApi } from "@/hooks/use-api";
import { ApiError, post } from "@/lib/http";
import { FormError } from "./bits";

const ERRORS: Record<string, string> = {
  cancelled: "Sign-in was cancelled.",
  invalid: "We could not verify that sign-in. Please try again.",
  no_account: "No KVL account is linked to that sign-in. Sign in with your password, then link it under Account.",
  account_unavailable: "This account is not available. Verify your email or contact support.",
  session: "Your session expired while linking. Sign in and try again.",
};

/** "Continue with Google / Microsoft" buttons; only providers the server has configured are shown. */
export function SsoButtons() {
  const providers = useApi<{ id: string; label: string }[]>("/auth/sso/providers");
  const [err, setErr] = React.useState<string>();
  if (!providers.data?.length) return null;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-xs text-faint"><span className="h-px flex-1 bg-border" />or<span className="h-px flex-1 bg-border" /></div>
      {providers.data.map((p) => (
        <Button key={p.id} type="button" variant="outline" size="lg" className="w-full justify-center" onClick={async () => {
          try {
            const r = await post<{ url: string }>(`/auth/sso/${p.id}/start`, { mode: "login" });
            window.location.assign(r.url);
          } catch (e) {
            setErr(e instanceof ApiError ? e.message : "Could not start sign-in.");
          }
        }}>Continue with {p.label}</Button>
      ))}
      <FormError message={err} />
    </div>
  );
}

export function ssoErrorText(code: string | null): string | undefined {
  return code ? (ERRORS[code] ?? "Sign-in failed. Please try again.") : undefined;
}

/** Second step after SSO for accounts with an authenticator app. */
export function SsoTwoFactor({ onDone }: { onDone: () => Promise<void> }) {
  const [code, setCode] = React.useState("");
  const [pending, setPending] = React.useState(false);
  const [err, setErr] = React.useState<string>();
  return (
    <form className="space-y-4" onSubmit={async (e) => {
      e.preventDefault();
      setPending(true);
      setErr(undefined);
      try {
        await post("/auth/sso/2fa", { code });
        await onDone();
      } catch (x) {
        setErr(x instanceof ApiError ? x.message : "Could not verify the code.");
      } finally {
        setPending(false);
      }
    }}>
      <p className="text-sm text-muted">Your account uses two-factor authentication. Enter the code from your authenticator app to finish signing in.</p>
      <Field label="Authenticator code" required>{(p) => <Input {...p} inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={code} onChange={(e) => setCode(e.target.value)} autoFocus />}</Field>
      <FormError message={err} />
      <Button type="submit" size="lg" className="w-full justify-center" loading={pending}>Verify and sign in</Button>
    </form>
  );
}
