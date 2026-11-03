"use client";

import * as React from "react";
import QRCode from "qrcode";
import { Copy, KeyRound, Laptop, LogOut, ShieldCheck, Smartphone, UserCircle } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Field, Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConfirmDialog } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/components/providers/auth-provider";
import { PasswordInput, PasswordMeter, FormError } from "@/components/auth/bits";
import { DetailList, fmtDate } from "@/components/resource/helpers";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { TableSkeleton } from "@/components/ui/skeleton";
import { useApi } from "@/hooks/use-api";
import { ApiError, del, post } from "@/lib/http";
import { ApiKeysPanel } from "@/components/security/api-keys-panel";
import { WebhooksPanel } from "@/components/security/webhooks-panel";

function ProfileTab() {
  const { me } = useAuth();
  if (!me) return null;
  const active = me.memberships.find((m) => m.id === me.active_membership_id);
  return (
    <Card>
      <CardHeader title="Profile" description="Your account details." />
      <CardBody>
        <DetailList
          items={[
            ["Name", me.user.name],
            ["Email", <span key="e" className="flex items-center justify-end gap-2">{me.user.email}{me.user.email_verified && <Badge tone="success" dot>Verified</Badge>}</span>],
            ["Role", active?.role_label ?? "—"],
            ["Account status", me.tenant_status ?? "n/a"],
            ["Two-factor authentication", me.user.totp_enabled ? "Enabled" : "Off"],
          ]}
        />
      </CardBody>
    </Card>
  );
}

function ChangePassword() {
  const toast = useToast();
  const [cur, setCur] = React.useState("");
  const [pw, setPw] = React.useState("");
  const [err, setErr] = React.useState<ApiError>();
  const [busy, setBusy] = React.useState(false);
  return (
    <Card>
      <CardHeader title="Change password" description="Other devices are signed out when you change it." />
      <CardBody>
        <form
          noValidate
          className="max-w-md space-y-4"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setErr(undefined);
            try {
              await post("/auth/password/change", { current_password: cur, new_password: pw });
              setCur("");
              setPw("");
              toast({ title: "Password changed", tone: "success" });
            } catch (x) {
              setErr(x as ApiError);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="Current password" required>{(p) => <PasswordInput {...p} value={cur} onChange={setCur} />}</Field>
          <Field label="New password" required>{(p) => <PasswordInput {...p} autoComplete="new-password" value={pw} onChange={setPw} />}</Field>
          <PasswordMeter value={pw} />
          <FormError message={err?.message} />
          <Button type="submit" loading={busy} disabled={!cur || !pw}>Update password</Button>
        </form>
      </CardBody>
    </Card>
  );
}

function TwoFactor() {
  const { me, reload } = useAuth();
  const toast = useToast();
  const [setup, setSetup] = React.useState<{ secret: string; otpauth_uri: string; qr: string }>();
  const [code, setCode] = React.useState("");
  const [err, setErr] = React.useState<ApiError>();
  const [busy, setBusy] = React.useState(false);
  const [disable, setDisable] = React.useState(false);
  const [pw, setPw] = React.useState("");
  if (!me) return null;

  const start = async () => {
    setBusy(true);
    try {
      const r = await post<{ secret: string; otpauth_uri: string }>("/auth/2fa/setup");
      setSetup({ ...r, qr: await QRCode.toDataURL(r.otpauth_uri, { margin: 1, width: 192 }) });
    } catch (x) {
      setErr(x as ApiError);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader
        title="Two-factor authentication"
        description="Require a code from an authenticator app when you sign in."
        actions={<Badge tone={me.user.totp_enabled ? "success" : "neutral"} dot>{me.user.totp_enabled ? "Enabled" : "Off"}</Badge>}
      />
      <CardBody className="space-y-4">
        {!me.user.totp_enabled && !setup && (
          <Button onClick={start} loading={busy}><ShieldCheck className="h-4 w-4" /> Set up authenticator</Button>
        )}
        {setup && !me.user.totp_enabled && (
          <div className="grid gap-5 sm:grid-cols-[auto_1fr]">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={setup.qr} alt="QR code for your authenticator app" width={192} height={192} className="rounded-lg border border-border bg-white p-1" />
            <div className="space-y-3">
              <p className="text-sm text-muted">Scan the QR code, or enter this key manually. It is shown once.</p>
              <div className="flex items-center gap-2 rounded-md bg-subtle px-3 py-2">
                <code className="tnum min-w-0 flex-1 truncate font-mono text-sm">{setup.secret}</code>
                <button type="button" aria-label="Copy key" onClick={() => navigator.clipboard.writeText(setup.secret).then(() => toast({ title: "Copied", tone: "success" }))} className="text-muted hover:text-text"><Copy className="h-4 w-4" /></button>
              </div>
              <Field label="Enter the 6-digit code to confirm">{(p) => <Input {...p} inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} />}</Field>
              <FormError message={err?.message} />
              <Button
                loading={busy}
                disabled={code.length < 6}
                onClick={async () => {
                  setBusy(true);
                  setErr(undefined);
                  try {
                    await post("/auth/2fa/enable", { code });
                    setSetup(undefined);
                    setCode("");
                    await reload();
                    toast({ title: "Two-factor authentication enabled", tone: "success" });
                  } catch (x) {
                    setErr(x as ApiError);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Enable
              </Button>
            </div>
          </div>
        )}
        {me.user.totp_enabled && (
          <>
            <p className="text-sm text-muted">Disabling requires your password and a current code.</p>
            <Button variant="outline" onClick={() => setDisable(true)}>Disable two-factor</Button>
            <ConfirmDisable open={disable} onOpenChange={setDisable} onDone={reload} pw={pw} setPw={setPw} />
          </>
        )}
      </CardBody>
    </Card>
  );
}

function ConfirmDisable({ open, onOpenChange, onDone, pw, setPw }: { open: boolean; onOpenChange: (o: boolean) => void; onDone: () => Promise<void>; pw: string; setPw: (v: string) => void }) {
  const [code, setCode] = React.useState("");
  const [err, setErr] = React.useState<string>();
  const toast = useToast();
  if (!open) return null;
  return (
    <div className="max-w-sm space-y-3 rounded-lg border border-border p-4">
      <Field label="Password">{(p) => <PasswordInput {...p} value={pw} onChange={setPw} />}</Field>
      <Field label="Authenticator code">{(p) => <Input {...p} inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} />}</Field>
      <FormError message={err} />
      <div className="flex gap-2">
        <Button variant="danger" onClick={async () => { try { await post("/auth/2fa/disable", { password: pw, code }); await onDone(); onOpenChange(false); toast({ title: "Two-factor disabled", tone: "info" }); } catch (x) { setErr((x as ApiError).message); } }}>Disable</Button>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
      </div>
    </div>
  );
}

interface SessionRow { id: string; ip: string | null; user_agent: string | null; created_at: string; last_used_at: string | null; current: boolean }

function Sessions() {
  const { data, loading, error, reload } = useApi<SessionRow[]>("/auth/sessions");
  const toast = useToast();
  const [target, setTarget] = React.useState<SessionRow | null>(null);
  const [all, setAll] = React.useState(false);
  const device = (ua?: string | null) => (/mobile|android|iphone/i.test(ua ?? "") ? Smartphone : Laptop);
  return (
    <Card>
      <CardHeader title="Active sessions" description="Devices currently signed in to your account." actions={<Button size="sm" variant="outline" onClick={() => setAll(true)}><LogOut className="h-4 w-4" /> Sign out others</Button>} />
      {loading ? <TableSkeleton rows={3} cols={2} /> : error ? <ErrorState onRetry={reload} /> : !data?.length ? <EmptyState title="No active sessions" /> : (
        <ul className="divide-y divide-border border-t border-border">
          {data.map((s) => {
            const Icon = device(s.user_agent);
            return (
              <li key={s.id} className="flex items-center gap-3 px-5 py-3">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-subtle text-muted"><Icon className="h-4 w-4" /></span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-text">{s.user_agent || "Unknown device"} {s.current && <Badge tone="primary">This device</Badge>}</p>
                  <p className="text-xs text-muted">{s.ip ?? "unknown IP"} · signed in {fmtDate(s.created_at)}</p>
                </div>
                {!s.current && <Button size="sm" variant="outline" onClick={() => setTarget(s)}>Revoke</Button>}
              </li>
            );
          })}
        </ul>
      )}
      <ConfirmDialog open={!!target} onOpenChange={(o) => !o && setTarget(null)} destructive title="Revoke this session?" description="That device will be signed out immediately." confirmLabel="Revoke" onConfirm={async () => { await del(`/auth/sessions/${target!.id}`); toast({ title: "Session revoked", tone: "success" }); reload(); }} />
      <ConfirmDialog open={all} onOpenChange={setAll} destructive title="Sign out all other devices?" description="Every session except this one will be revoked." confirmLabel="Sign out others" onConfirm={async () => { await post("/auth/sessions/revoke-others"); toast({ title: "Other sessions revoked", tone: "success" }); reload(); }} />
    </Card>
  );
}

interface HistoryRow { id: string; outcome: string; suspicious: boolean; ip: string | null; user_agent: string | null; created_at: string }

function History() {
  const { data, loading, error, reload } = useApi<HistoryRow[]>("/auth/login-history");
  return (
    <Card>
      <CardHeader title="Sign-in activity" description="Recent sign-in attempts on your account." />
      {loading ? <TableSkeleton rows={4} cols={3} /> : error ? <ErrorState onRetry={reload} /> : !data?.length ? <EmptyState title="No activity yet" /> : (
        <ul className="divide-y divide-border border-t border-border">
          {data.map((h) => (
            <li key={h.id} className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 text-sm">
              <div className="min-w-0">
                <p className="font-medium text-text">{h.outcome.replace(/_/g, " ")} {h.suspicious && <Badge tone="warning" dot>New device</Badge>}</p>
                <p className="truncate text-xs text-muted">{h.ip ?? "unknown IP"} · {h.user_agent ?? "unknown device"}</p>
              </div>
              <span className="text-xs text-muted">{fmtDate(h.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Developer() {
  const { can, me } = useAuth();
  const tenantScoped = me?.scope !== "platform";
  return (
    <div className="space-y-4">
      {can("apikeys:read") && <ApiKeysPanel tenantScoped={tenantScoped} />}
      {can("webhooks:read") && <WebhooksPanel tenantScoped={tenantScoped} />}
      {!can("apikeys:read") && !can("webhooks:read") && <EmptyState title="Developer tools are not enabled for your role" />}
    </div>
  );
}

export function AccountPage() {
  return (
    <>
      <PageHeader title="Profile & security" description="Manage your account, password, two-factor authentication and devices." />
      <Tabs defaultValue="profile">
        <TabsList className="mb-4 overflow-x-auto">
          <TabsTrigger value="profile"><UserCircle className="mr-1.5 inline h-4 w-4" />Profile</TabsTrigger>
          <TabsTrigger value="security"><KeyRound className="mr-1.5 inline h-4 w-4" />Security</TabsTrigger>
          <TabsTrigger value="sessions"><Laptop className="mr-1.5 inline h-4 w-4" />Sessions</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
          <TabsTrigger value="developer">Developer</TabsTrigger>
        </TabsList>
        <TabsContent value="profile"><ProfileTab /></TabsContent>
        <TabsContent value="security" className="space-y-4"><ChangePassword /><TwoFactor /></TabsContent>
        <TabsContent value="sessions"><Sessions /></TabsContent>
        <TabsContent value="activity"><History /></TabsContent>
        <TabsContent value="developer"><Developer /></TabsContent>
      </Tabs>
    </>
  );
}
