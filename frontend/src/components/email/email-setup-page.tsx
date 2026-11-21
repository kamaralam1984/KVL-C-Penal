"use client";

import * as React from "react";
import { CheckCircle2, CircleAlert, CircleDashed, Copy, Mail, Wand2 } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { RequirePerm, Can } from "@/components/resource/helpers";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Field, Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { useApi } from "@/hooks/use-api";
import { ApiError, get, post } from "@/lib/http";

interface Provider { id: string; label: string; dkim_selector: string | null; smtp: { host: string; port: number; security: string } }
interface Rec { key: string; purpose: string; type: string; name: string; content: string; priority: number | null; state: "ok" | "missing" | "conflict"; action: "create" | "update" | null; record_id: string | null; current?: string; note?: string }
interface Plan { domain: string; label: string; records: Rec[]; synced: boolean; other_mx: string[]; smtp: Provider["smtp"]; ready: boolean }
interface DomainLite { id: string; hostname: string; verification_state: string }

const msg = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong");
const ICON = { ok: <CheckCircle2 className="h-4 w-4 text-success" aria-label="done" />, missing: <CircleDashed className="h-4 w-4 text-muted" aria-label="missing" />, conflict: <CircleAlert className="h-4 w-4 text-warning" aria-label="needs attention" /> };

function mergedSpf(current: string, wanted: string): string {
  const inc = wanted.split(/\s+/).find((x) => x.startsWith("include:")) ?? "";
  const parts = current.replace(/^"|"$/g, "").split(/\s+/);
  return parts.includes(inc) ? parts.join(" ") : [parts[0], inc, ...parts.slice(1)].join(" ");
}

/** Business email with a hosted provider: shows exactly which DNS records are needed and their status. */
export function EmailSetupPage() {
  const toast = useToast();
  const providers = useApi<Provider[]>("/email-setup/providers");
  const domains = useApi<{ items: DomainLite[] }>("/domains", { limit: 100 });
  const [domain, setDomain] = React.useState("");
  const [provider, setProvider] = React.useState("zoho_in");
  const [dkim, setDkim] = React.useState("");
  const [plan, setPlan] = React.useState<Plan | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [err, setErr] = React.useState<string>();
  const doms = (domains.data?.items ?? []).filter((d) => d.verification_state === "verified");

  async function check() {
    if (!domain) return;
    setBusy("check");
    setErr(undefined);
    try {
      setPlan(await get<Plan>(`/email-setup/${encodeURIComponent(domain)}`, { provider, ...(dkim.trim() ? { dkim_value: dkim.trim() } : {}) }));
    } catch (e) {
      setErr(msg(e));
    } finally {
      setBusy(null);
    }
  }

  async function prepare(r: Rec) {
    setBusy(r.key);
    try {
      const content = r.state === "conflict" && r.key === "spf" && r.current ? mergedSpf(r.current, r.content) : r.content;
      const body = { action: r.action, ...(r.action === "update" ? { record_id: r.record_id } : {}), record: { type: r.type, name: r.name, content, ttl: 1, priority: r.priority ?? undefined }, protected_ack: true };
      await post(`/dns/${encodeURIComponent(plan!.domain)}/changes`, body);
      toast({ title: "DNS change prepared", description: "Review and confirm it on the SSL & DNS page. Nothing is live until you confirm.", tone: "success" });
    } catch (e) {
      toast({ title: "Could not prepare the change", description: msg(e), tone: "danger" });
    } finally {
      setBusy(null);
    }
  }

  const copy = (t: string) => void navigator.clipboard?.writeText(t).then(() => toast({ title: "Copied", tone: "success" }));

  return (
    <RequirePerm perm="dns:read">
      <PageHeader title="Email" description="Use your own domain for email (you@yourdomain) with Google Workspace, Zoho Mail or Microsoft 365. The panel sets up DNS; mailboxes live at the provider." />
      <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
        <Card>
          <CardHeader title="Set up" description="1. Create the account at the provider. 2. Check the records here. 3. Add the missing ones." actions={<Mail className="h-5 w-5 text-primary" aria-hidden />} />
          <CardBody className="space-y-3">
            <Field label="Domain">{(p) => <Select {...p} value={domain} onChange={(e) => { setDomain(e.target.value); setPlan(null); }}><option value="">Choose…</option>{doms.map((d) => <option key={d.id} value={d.hostname}>{d.hostname}</option>)}</Select>}</Field>
            <Field label="Email provider">{(p) => <Select {...p} value={provider} onChange={(e) => { setProvider(e.target.value); setPlan(null); }}>{providers.data?.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}</Select>}</Field>
            <Field label="DKIM value (optional)" hint="Paste the DKIM TXT value from the provider's admin console, starting with v=DKIM1;">{(p) => <textarea {...p} rows={3} className="w-full rounded-md border border-border bg-surface p-2 font-mono text-xs text-text" value={dkim} onChange={(e) => setDkim(e.target.value)} />}</Field>
            {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err}</p>}
            <Button disabled={!domain} loading={busy === "check"} onClick={() => void check()}>Check DNS</Button>
            {!doms.length && !domains.loading && <p className="text-xs text-muted">Add and verify a domain first on the Domains page.</p>}
          </CardBody>
        </Card>

        {plan && (
          <Card>
            <CardHeader title={`${plan.label} for ${plan.domain}`} description={plan.ready ? "All records are in place. Mail should start flowing within an hour." : "Add the records marked as missing or needing attention."} />
            <CardBody className="space-y-3">
              {!plan.synced && <p className="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">The domain&apos;s DNS has not been synced from Cloudflare yet, so everything shows as missing. Sync it on the SSL &amp; DNS page first.</p>}
              {!!plan.other_mx.length && <p className="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">Other mail servers are still set ({plan.other_mx.join(", ")}). Remove them when you switch, or mail may go to the old provider.</p>}
              <ul className="divide-y divide-border rounded-md border border-border">
                {plan.records.map((r) => (
                  <li key={r.key} className="space-y-1 px-3 py-2.5 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">{ICON[r.state]}<span className="font-medium text-text">{r.purpose}</span></div>
                      <div className="flex gap-1">
                        <Button size="sm" variant="ghost" onClick={() => copy(r.content)}><Copy className="h-3.5 w-3.5" /><span className="sr-only">Copy value</span></Button>
                        {r.action && <Can perm="dns:write"><Button size="sm" variant="outline" loading={busy === r.key} onClick={() => void prepare(r)}><Wand2 className="h-3.5 w-3.5" />{r.state === "conflict" ? "Merge" : "Add"}</Button></Can>}
                      </div>
                    </div>
                    <p className="font-mono text-xs break-all text-muted">{r.type} {r.name}{r.priority != null ? ` · priority ${r.priority}` : ""} → {r.content}</p>
                    {r.note && <p className="text-xs text-warning">{r.note}</p>}
                  </li>
                ))}
              </ul>
              <Can perm="dns:write" fallback={<p className="text-xs text-muted">Add these records at your DNS provider, or open a support ticket and we will add them for you.</p>}><span /></Can>
              <p className="text-xs text-muted">Send website mail through this mailbox: SMTP {plan.smtp.host}:{plan.smtp.port} ({plan.smtp.security}).</p>
            </CardBody>
          </Card>
        )}
      </div>
    </RequirePerm>
  );
}
