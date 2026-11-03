"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { DevOtpHint, FormError, PasswordInput, PasswordMeter, useCooldown } from "./bits";
import { ApiError, get, post } from "@/lib/http";
import { homeFor, useAuth } from "@/components/providers/auth-provider";
import { Segmented } from "@/components/ui/tabs";

function useSubmit() {
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState<ApiError>();
  const run = async (fn: () => Promise<void>) => {
    setPending(true);
    setError(undefined);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "network", "Cannot reach the server. Check your connection."));
    } finally {
      setPending(false);
    }
  };
  return { pending, error, run, setError };
}

function errText(e?: ApiError) {
  if (!e) return undefined;
  if (e.code === "rate_limited") return `Too many attempts. Try again in ${(e.extra?.retry_after as number) ?? 60}s.`;
  if (e.fields?.length) return e.fields.map((f) => f.message).join(" ");
  return e.message;
}

export function LoginForm() {
  const router = useRouter();
  const sp = useSearchParams();
  const { reload } = useAuth();
  const [mode, setMode] = React.useState<"password" | "code">("password");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [totp, setTotp] = React.useState("");
  const [needTotp, setNeedTotp] = React.useState(false);
  const [codeSent, setCodeSent] = React.useState(false);
  const [code, setCode] = React.useState("");
  const { pending, error, run } = useSubmit();
  const cd = useCooldown(30);

  async function finish() {
    await reload();
    const me = await get<{ role: string | null }>("/auth/me");
    const next = sp.get("next");
    router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : homeFor(me.role));
  }

  const submitPassword = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await post("/auth/login", { email, password, ...(needTotp ? { totp_code: totp } : {}) });
      await finish();
    });
  };

  const sendCode = () =>
    run(async () => {
      await post("/auth/otp/request", { email, purpose: "login" });
      setCodeSent(true);
      cd.start();
    });

  const submitCode = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await post("/auth/otp/login", { email, code });
      await finish();
    });
  };

  // surface "needs 2FA" as a step, not an error
  const totpRequired = error?.code === "totp_required";
  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (totpRequired) setNeedTotp(true);
  }, [totpRequired]);

  return (
    <div className="space-y-5">
      {sp.get("reset") && <p role="status" className="rounded-md bg-success/10 px-3 py-2 text-sm text-success">Password updated. Sign in with your new password.</p>}
      <Segmented label="Sign-in method" value={mode} onChange={setMode} options={[{ value: "password", label: "Password" }, { value: "code", label: "Email code" }]} />
      {mode === "password" ? (
        <form onSubmit={submitPassword} className="space-y-4" noValidate>
          <Field label="Email" required>{(p) => <Input {...p} type="email" autoComplete="username" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" />}</Field>
          <Field label="Password" required>{(p) => <PasswordInput {...p} value={password} onChange={setPassword} />}</Field>
          {needTotp && (
            <Field label="Authenticator code" required hint="Open your authenticator app and enter the 6-digit code.">
              {(p) => <Input {...p} inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={totp} onChange={(e) => setTotp(e.target.value)} autoFocus />}
            </Field>
          )}
          {!totpRequired && <FormError message={errText(error)} />}
          {error?.code === "email_not_verified" && (
            <p className="text-sm"><Link className="font-medium text-primary hover:underline" href={`/verify-email?email=${encodeURIComponent(email)}`}>Verify your email</Link></p>
          )}
          <div className="flex justify-end"><Link href="/forgot-password" className="text-sm font-medium text-primary hover:underline">Forgot password?</Link></div>
          <Button type="submit" size="lg" className="w-full justify-center" loading={pending}>Sign in</Button>
        </form>
      ) : (
        <form onSubmit={submitCode} className="space-y-4" noValidate>
          <Field label="Email" required>{(p) => <Input {...p} type="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" />}</Field>
          {codeSent && (
            <>
              <Field label="6-digit code" required hint="If an account exists, a code was sent. It expires in 10 minutes.">
                {(p) => <Input {...p} inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} autoFocus />}
              </Field>
              <DevOtpHint email={email} />
            </>
          )}
          <FormError message={errText(error)} />
          {codeSent ? (
            <div className="space-y-2">
              <Button type="submit" size="lg" className="w-full justify-center" loading={pending}>Verify and sign in</Button>
              <Button type="button" variant="ghost" className="w-full justify-center" disabled={cd.left > 0 || pending} onClick={sendCode}>{cd.left > 0 ? `Resend in ${cd.left}s` : "Resend code"}</Button>
            </div>
          ) : (
            <Button type="button" size="lg" className="w-full justify-center" loading={pending} disabled={!email} onClick={sendCode}>Email me a code</Button>
          )}
        </form>
      )}
    </div>
  );
}

export function RegisterForm() {
  const router = useRouter();
  const [f, setF] = React.useState({ name: "", email: "", organization: "", password: "" });
  const { pending, error, run } = useSubmit();
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF((s) => ({ ...s, [k]: e.target.value }));
  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        void run(async () => {
          await post("/auth/register", { ...f, organization: f.organization || undefined });
          router.push(`/verify-email?email=${encodeURIComponent(f.email)}`);
        });
      }}
    >
      <Field label="Full name" required>{(p) => <Input {...p} autoComplete="name" value={f.name} onChange={set("name")} />}</Field>
      <Field label="Work email" required>{(p) => <Input {...p} type="email" inputMode="email" autoComplete="email" value={f.email} onChange={set("email")} />}</Field>
      <Field label="Organization" hint="Optional. Shown on invoices and in your account.">{(p) => <Input {...p} autoComplete="organization" value={f.organization} onChange={set("organization")} />}</Field>
      <Field label="Password" required>{(p) => <PasswordInput {...p} autoComplete="new-password" value={f.password} onChange={(v) => setF((s) => ({ ...s, password: v }))} />}</Field>
      <PasswordMeter value={f.password} />
      <FormError message={errText(error)} />
      <Button type="submit" size="lg" className="w-full justify-center" loading={pending}>Create account</Button>
    </form>
  );
}

export function VerifyEmailForm() {
  const router = useRouter();
  const sp = useSearchParams();
  const [email, setEmail] = React.useState(sp.get("email") ?? "");
  const [code, setCode] = React.useState("");
  const [done, setDone] = React.useState(false);
  const { pending, error, run } = useSubmit();
  const cd = useCooldown(30);
  if (done)
    return (
      <div className="space-y-4 text-center">
        <p className="rounded-md bg-success/10 px-3 py-3 text-sm text-success" role="status">Email verified. You can sign in now.</p>
        <Button size="lg" className="w-full justify-center" onClick={() => router.push("/login")}>Continue to sign in</Button>
      </div>
    );
  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        void run(async () => {
          await post("/auth/verify-email", { email, code });
          setDone(true);
        });
      }}
    >
      <Field label="Email" required>{(p) => <Input {...p} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
      <Field label="6-digit code" required hint="Check your inbox. Codes expire in 10 minutes.">{(p) => <Input {...p} inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} autoFocus />}</Field>
      <DevOtpHint email={email} />
      <FormError message={errText(error)} />
      <Button type="submit" size="lg" className="w-full justify-center" loading={pending}>Verify email</Button>
      <Button
        type="button"
        variant="ghost"
        className="w-full justify-center"
        disabled={!email || cd.left > 0}
        onClick={() => void run(async () => { await post("/auth/otp/request", { email, purpose: "verify_email" }); cd.start(); })}
      >
        {cd.left > 0 ? `Resend in ${cd.left}s` : "Resend code"}
      </Button>
    </form>
  );
}

export function ForgotForm() {
  const router = useRouter();
  const [email, setEmail] = React.useState("");
  const { pending, error, run } = useSubmit();
  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        void run(async () => {
          await post("/auth/password/forgot", { email });
          router.push(`/reset-password?email=${encodeURIComponent(email)}`);
        });
      }}
    >
      <Field label="Email" required>{(p) => <Input {...p} type="email" inputMode="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
      <FormError message={errText(error)} />
      <Button type="submit" size="lg" className="w-full justify-center" loading={pending}>Send reset code</Button>
    </form>
  );
}

export function ResetForm() {
  const router = useRouter();
  const sp = useSearchParams();
  const [email, setEmail] = React.useState(sp.get("email") ?? "");
  const [code, setCode] = React.useState("");
  const [pw, setPw] = React.useState("");
  const { pending, error, run } = useSubmit();
  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        void run(async () => {
          await post("/auth/password/reset", { email, code, new_password: pw });
          router.push("/login?reset=1");
        });
      }}
    >
      <Field label="Email" required>{(p) => <Input {...p} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
      <Field label="Reset code" required hint="If an account exists, a code was sent.">{(p) => <Input {...p} inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} />}</Field>
      <DevOtpHint email={email} />
      <Field label="New password" required>{(p) => <PasswordInput {...p} autoComplete="new-password" value={pw} onChange={setPw} />}</Field>
      <PasswordMeter value={pw} />
      <FormError message={errText(error)} />
      <Button type="submit" size="lg" className="w-full justify-center" loading={pending}>Reset password</Button>
    </form>
  );
}

export function AcceptInviteForm() {
  const router = useRouter();
  const sp = useSearchParams();
  const { reload } = useAuth();
  const token = sp.get("token") ?? "";
  const [name, setName] = React.useState("");
  const [pw, setPw] = React.useState("");
  const { pending, error, run } = useSubmit();
  if (!token) return <FormError message="This invitation link is incomplete. Ask your administrator to resend it." />;
  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        void run(async () => {
          await post("/auth/invitations/accept", { token, name, password: pw });
          await reload();
          router.replace("/");
        });
      }}
    >
      <Field label="Your name" required>{(p) => <Input {...p} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
      <Field label="Choose a password" required>{(p) => <PasswordInput {...p} autoComplete="new-password" value={pw} onChange={setPw} />}</Field>
      <PasswordMeter value={pw} />
      <FormError message={errText(error)} />
      <Button type="submit" size="lg" className="w-full justify-center" loading={pending}>Accept invitation</Button>
    </form>
  );
}
