"use client";

import Link from "next/link";
import { ShieldCheck, Zap, Layers } from "lucide-react";
import { Brand } from "@/components/shell/brand";
import { AccentLine } from "@/components/ui/accent-line";
import { ThemeSelector } from "@/components/shell/theme-selector";
import { AnimatedIcon } from "@/components/ui/animated-icon";

const points = [
  { icon: ShieldCheck, title: "Secure by design", text: "Argon2id, rotating sessions, 2FA and tenant isolation on every request." },
  { icon: Zap, title: "Built for speed", text: "Lean pages, instant navigation and an app-like mobile experience." },
  { icon: Layers, title: "One panel, every role", text: "Admins, hosting teams, customers and resellers in a single platform." },
];

/** Split-screen on desktop, full-bleed app screen on phones. */
export function AuthShell({ title, subtitle, children, footer }: { title: string; subtitle?: string; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      <aside className="relative hidden overflow-hidden bg-[#0a1c20] p-10 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="absolute inset-x-0 top-0"><AccentLine /></div>
        <div aria-hidden className="pointer-events-none absolute -top-24 -right-24 h-80 w-80 rounded-full opacity-30 blur-3xl" style={{ background: "radial-gradient(circle, var(--gold) 0%, transparent 70%)" }} />
        <div aria-hidden className="pointer-events-none absolute -bottom-32 -left-16 h-96 w-96 rounded-full opacity-20 blur-3xl" style={{ background: "radial-gradient(circle, var(--silver) 0%, transparent 70%)" }} />
        <div className="relative [&_p]:!text-white [&_p+p]:!text-white/60"><Brand /></div>
        <div className="relative max-w-md">
          <h2 className="text-3xl font-semibold tracking-tight">Run your hosting business from one calm, fast control panel.</h2>
          <div className="my-6 w-24"><AccentLine /></div>
          <ul className="space-y-5">
            {points.map((p) => (
              <li key={p.title} className="flex gap-3">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-white/10 text-[var(--gold-soft)]">
                  <AnimatedIcon icon={p.icon} idle="float" />
                </span>
                <div>
                  <p className="font-medium">{p.title}</p>
                  <p className="text-sm text-white/65">{p.text}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-white/45">© KVL Hosting. Private infrastructure control panel.</p>
      </aside>

      <main id="main-content" className="relative flex flex-col px-5 pt-[calc(var(--safe-top)+1.25rem)] pb-[calc(var(--safe-bottom)+1.5rem)] sm:px-10">
        <div className="flex items-center justify-between">
          <Link href="/" aria-label="KVL home" className="lg:invisible"><Brand /></Link>
          <ThemeSelector />
        </div>
        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-8">
          <h1 className="text-2xl font-semibold tracking-tight text-text">{title}</h1>
          <div className="my-3 w-14"><AccentLine /></div>
          {subtitle && <p className="mb-6 text-base text-muted">{subtitle}</p>}
          {children}
          {footer && <div className="mt-6 text-center text-sm text-muted">{footer}</div>}
        </div>
      </main>
    </div>
  );
}
