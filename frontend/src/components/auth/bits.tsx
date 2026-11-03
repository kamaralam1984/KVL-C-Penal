"use client";

import * as React from "react";
import { Eye, EyeOff, Info } from "lucide-react";
import { Input } from "@/components/ui/input";
import { get } from "@/lib/http";
import { cn } from "@/lib/cn";

export function PasswordInput({ id, value, onChange, autoComplete = "current-password", ...p }: { id?: string; value: string; onChange: (v: string) => void; autoComplete?: string } & Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange" | "value">) {
  const [show, setShow] = React.useState(false);
  return (
    <div className="relative">
      <Input {...p} id={id} type={show ? "text" : "password"} value={value} onChange={(e) => onChange(e.target.value)} autoComplete={autoComplete} className="pr-10" />
      <button type="button" onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"} className="absolute top-1/2 right-1.5 grid h-7 w-7 -translate-y-1/2 place-items-center rounded text-faint hover:bg-subtle hover:text-text">
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
}

export function scorePassword(pw: string) {
  let s = 0;
  if (pw.length >= 10) s++;
  if (pw.length >= 14) s++;
  if (/[A-Za-z]/.test(pw) && /\d/.test(pw)) s++;
  if (/[^A-Za-z0-9]/.test(pw) && /[A-Z]/.test(pw) && /[a-z]/.test(pw)) s++;
  return s;
}

export function PasswordMeter({ value }: { value: string }) {
  const s = scorePassword(value);
  const labels = ["Too weak", "Weak", "Okay", "Good", "Strong"];
  const colors = ["bg-danger", "bg-danger", "bg-warning", "bg-success", "bg-success"];
  if (!value) return <p className="text-xs text-muted">At least 10 characters with letters and numbers.</p>;
  return (
    <div aria-live="polite">
      <div className="flex gap-1" aria-hidden>
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className={cn("h-1 flex-1 rounded-full transition-colors duration-300", i < s ? colors[s] : "bg-subtle")} />
        ))}
      </div>
      <p className="mt-1 text-xs text-muted">{labels[s]}</p>
    </div>
  );
}

export function FormError({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">
      {message}
    </p>
  );
}

/** Development convenience: shows the latest queued code because no SMTP transport exists locally. Hidden when the dev endpoint is unavailable. */
export function DevOtpHint({ email }: { email: string }) {
  const [code, setCode] = React.useState<string>();
  const [busy, setBusy] = React.useState(false);
  if (process.env.NODE_ENV === "production" || !email) return null;
  const load = async () => {
    setBusy(true);
    try {
      const rows = await get<{ body: string }[]>("/dev/outbox", { email });
      setCode(rows[0]?.body.match(/(\d{6})/)?.[1] ?? "none queued");
    } catch {
      setCode("unavailable");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex items-center gap-2 rounded-md border border-dashed border-warning/50 bg-warning/8 px-3 py-2 text-xs text-warning">
      <Info className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span>Dev mode: no email server is connected.</span>
      <button type="button" onClick={load} disabled={busy} className="ml-auto font-semibold underline">
        {code ? <span className="tnum font-mono">{code}</span> : "Show code"}
      </button>
    </div>
  );
}

export function useCooldown(seconds: number) {
  const [left, setLeft] = React.useState(0);
  React.useEffect(() => {
    if (left <= 0) return;
    const t = setTimeout(() => setLeft((l) => l - 1), 1000);
    return () => clearTimeout(t);
  }, [left]);
  return { left, start: () => setLeft(seconds) };
}
