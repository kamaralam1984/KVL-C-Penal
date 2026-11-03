"use client";

import { Globe2, Lock, Rocket, Server } from "lucide-react";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import { Card } from "@/components/ui/card";

const features = [
  { icon: Server, title: "Servers & monitoring", text: "Live agent health, metrics and alerts for your private server." },
  { icon: Globe2, title: "Sites, domains & SSL", text: "Provision websites, verify domains and automate certificates." },
  { icon: Lock, title: "Secure multi-tenancy", text: "Strict tenant isolation, RBAC, audit trails and 2FA." },
  { icon: Rocket, title: "Billing & automation", text: "Plans, subscriptions, invoices and n8n workflows." },
];

export function FeatureGrid() {
  return (
    <ul className="mt-14 grid gap-4 sm:grid-cols-2">
      {features.map((f) => (
        <li key={f.title}>
          <Card className="h-full p-5 transition-all hover:-translate-y-0.5 hover:border-primary/50 hover:shadow-md">
            <span className="mb-3 grid h-10 w-10 place-items-center rounded-lg bg-primary-soft text-primary">
              <AnimatedIcon icon={f.icon} idle="float" size={20} />
            </span>
            <h2 className="text-lg font-semibold text-text">{f.title}</h2>
            <p className="mt-1 text-sm text-muted">{f.text}</p>
          </Card>
        </li>
      ))}
    </ul>
  );
}
