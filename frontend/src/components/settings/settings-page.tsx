"use client";

import * as React from "react";
import { Plus, ShieldCheck, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { RequirePerm, Can, StatusPill, fmtDate } from "@/components/resource/helpers";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { FormDialog } from "@/components/resource/form-dialog";
import { ResourceTable } from "@/components/resource/resource-table";
import { ConfirmDialog } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/components/providers/auth-provider";
import { useApi } from "@/hooks/use-api";
import { ApiError, del, post, put } from "@/lib/http";
import type { Column } from "@/components/ui/data-table";
import { TableSkeleton } from "@/components/ui/skeleton";

interface Setting { key: string; value: Record<string, number>; updated_at: string | null }
const LABELS: Record<string, { title: string; help: string }> = {
  trial_days: { title: "Trial length", help: "Days a new customer stays on trial." },
  grace_days: { title: "Grace period", help: "Days after a failed payment before suspension." },
  retention_days: { title: "Data retention", help: "Days data is kept after cancellation." },
  default_quotas: { title: "Default quotas", help: "Limits given to every new tenant." },
};

function SettingCard({ s, onSaved }: { s: Setting; onSaved: () => void }) {
  const toast = useToast();
  const { can } = useAuth();
  const [vals, setVals] = React.useState<Record<string, string>>(Object.fromEntries(Object.entries(s.value).map(([k, v]) => [k, String(v)])));
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string>();
  const meta = LABELS[s.key] ?? { title: s.key, help: "" };
  return (
    <Card>
      <CardHeader title={meta.title} description={meta.help} />
      <CardBody>
        <form className="grid gap-3 sm:grid-cols-2" onSubmit={async (e) => { e.preventDefault(); setBusy(true); setErr(undefined); try { await put(`/settings/${s.key}`, { value: Object.fromEntries(Object.entries(vals).map(([k, v]) => [k, Number(v)])) }); toast({ title: `${meta.title} saved`, tone: "success" }); onSaved(); } catch (x) { setErr((x as ApiError).message); } finally { setBusy(false); } }}>
          {Object.keys(vals).map((k) => (
            <Field key={k} label={k.replace(/_/g, " ")}>{(p) => <Input {...p} type="number" min={0} disabled={!can("settings:write")} value={vals[k]} onChange={(e) => setVals((v) => ({ ...v, [k]: e.target.value }))} />}</Field>
          ))}
          {err && <p role="alert" className="text-sm text-danger sm:col-span-2">{err}</p>}
          <Can perm="settings:write"><div className="sm:col-span-2"><Button type="submit" loading={busy}>Save</Button></div></Can>
        </form>
      </CardBody>
    </Card>
  );
}

function BusinessRules() {
  const { data, loading, reload } = useApi<Setting[]>("/settings");
  if (loading) return <TableSkeleton rows={3} cols={2} />;
  return <div className="grid gap-4 lg:grid-cols-2">{data?.map((s) => <SettingCard key={s.key + String(s.updated_at)} s={s} onSaved={reload} />)}</div>;
}

interface RoleRow { name: string; label: string; scope: string; privileged: boolean; permissions: string[] }

function Roles() {
  const toast = useToast();
  const { me } = useAuth();
  const roles = useApi<RoleRow[]>("/rbac/roles");
  const perms = useApi<string[]>("/rbac/permissions");
  const [role, setRole] = React.useState<string>("support");
  const [sel, setSel] = React.useState<Set<string>>(new Set());
  const [dirty, setDirty] = React.useState(false);
  const current = roles.data?.find((r) => r.name === role);
  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (current) { setSel(new Set(current.permissions)); setDirty(false); }
  }, [current]);
  const groups = React.useMemo(() => {
    const g: Record<string, string[]> = {};
    for (const p of perms.data ?? []) (g[p.split(":")[0]] ??= []).push(p);
    return g;
  }, [perms.data]);
  const editable = me?.role === "super_admin" && role !== "super_admin";
  if (roles.loading || perms.loading) return <TableSkeleton rows={5} cols={2} />;
  return (
    <Card>
      <CardHeader title="Roles & permissions" description="Deny by default. Changes apply immediately and are audited." actions={<ShieldCheck className="h-5 w-5 text-primary" aria-hidden />} />
      <CardBody className="space-y-4">
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Roles">
          {roles.data?.map((r) => (
            <button key={r.name} role="tab" aria-selected={r.name === role} onClick={() => setRole(r.name)} className={`rounded-full border px-3 py-1 text-sm font-medium ${r.name === role ? "border-primary bg-primary-soft text-primary" : "border-border text-muted hover:bg-subtle"}`}>{r.label}</button>
          ))}
        </div>
        {!editable && <p className="rounded-md bg-subtle px-3 py-2 text-xs text-muted">{role === "super_admin" ? "The platform owner role is immutable and holds every permission." : "Only the platform owner can edit permissions."}</p>}
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Object.entries(groups).map(([res, list]) => (
            <fieldset key={res} className="rounded-lg border border-border p-3">
              <legend className="px-1 text-xs font-semibold tracking-wide text-faint uppercase">{res}</legend>
              {list.map((p) => (
                <label key={p} className="flex items-center gap-2 py-0.5 text-sm text-text">
                  <input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" disabled={!editable} checked={sel.has(p)} onChange={(e) => { const n = new Set(sel); if (e.target.checked) n.add(p); else n.delete(p); setSel(n); setDirty(true); }} />
                  {p.split(":")[1]}
                </label>
              ))}
            </fieldset>
          ))}
        </div>
        {editable && <Button disabled={!dirty} onClick={async () => { try { await put(`/rbac/roles/${role}/permissions`, { permissions: [...sel] }); toast({ title: "Permissions updated", tone: "success" }); roles.reload(); } catch (x) { toast({ title: "Not saved", description: (x as ApiError).message, tone: "danger" }); } }}>Save permissions</Button>}
      </CardBody>
    </Card>
  );
}

interface UserRow { id: string; email: string; name: string; status: string; email_verified: boolean; totp_enabled: boolean; created_at: string }
interface InviteRow { id: string; email: string; role: string; expires_at: string; accepted: boolean; revoked: boolean }

function Team() {
  const toast = useToast();
  const [inviting, setInviting] = React.useState(false);
  const [rev, setRev] = React.useState<InviteRow | null>(null);
  const invites = useApi<InviteRow[]>("/staff/invitations");
  const { me } = useAuth();
  const roles = [{ value: "support", label: "Support Staff" }, ...(me?.role === "super_admin" ? [{ value: "hosting_admin", label: "Hosting Admin" }, { value: "super_admin", label: "Super Admin" }] : [])];
  const userCols: Column<UserRow>[] = [
    { id: "name", header: "Name", sortValue: (r) => r.name, cell: (r) => (<div><p className="font-medium text-text">{r.name}</p><p className="text-xs text-muted">{r.email}</p></div>) },
    { id: "status", header: "Status", cell: (r) => <StatusPill status={r.status} /> },
    { id: "2fa", header: "2FA", hideOnMobile: true, cell: (r) => (r.totp_enabled ? "On" : "Off") },
    { id: "joined", header: "Joined", hideOnMobile: true, sortValue: (r) => r.created_at, cell: (r) => fmtDate(r.created_at) },
  ];
  return (
    <div className="space-y-4">
      <ResourceTable<UserRow> path="/users" caption="Users" columns={userCols} searchText={(r) => `${r.name} ${r.email}`} onCreate={() => setInviting(true)} createLabel="Invite staff" canCreate />
      <Card>
        <CardHeader title="Pending invitations" actions={<Button size="sm" variant="outline" onClick={() => setInviting(true)}><Plus className="h-4 w-4" />Invite</Button>} />
        {invites.loading ? <TableSkeleton rows={2} cols={2} /> : (
          <ul className="divide-y divide-border border-t border-border">
            {invites.data?.filter((i) => !i.accepted && !i.revoked).map((i) => (
              <li key={i.id} className="flex items-center justify-between gap-3 px-5 py-3 text-sm"><div><p className="font-medium text-text">{i.email}</p><p className="text-xs text-muted">{i.role} · expires {fmtDate(i.expires_at)}</p></div><Button size="sm" variant="ghost" onClick={() => setRev(i)}><Trash2 className="h-4 w-4" /><span className="sr-only">Revoke</span></Button></li>
            ))}
            {!invites.data?.some((i) => !i.accepted && !i.revoked) && <li className="px-5 py-4 text-sm text-muted">No pending invitations.</li>}
          </ul>
        )}
      </Card>
      <FormDialog open={inviting} onOpenChange={setInviting} title="Invite staff member" description="They receive an email with a link valid for 7 days." fields={[{ name: "email", label: "Email", type: "email", required: true }, { name: "role", label: "Role", type: "select", required: true, defaultValue: "support", options: roles }]} submitLabel="Send invitation" onSubmit={async (v) => { await post("/staff/invitations", v); invites.reload(); }} successMessage="Invitation queued" />
      <ConfirmDialog open={!!rev} onOpenChange={(o) => !o && setRev(null)} destructive title="Revoke invitation?" description={`The link sent to ${rev?.email} will stop working.`} confirmLabel="Revoke" onConfirm={async () => { await del(`/staff/invitations/${rev!.id}`); toast({ title: "Invitation revoked", tone: "success" }); invites.reload(); }} />
    </div>
  );
}

export function SettingsPage() {
  return (
    <RequirePerm perm="settings:read">
      <PageHeader title="Settings" description="Business rules, roles and the platform team." />
      <Tabs defaultValue="rules">
        <TabsList className="mb-4 overflow-x-auto"><TabsTrigger value="rules">Business rules</TabsTrigger><TabsTrigger value="roles">Roles</TabsTrigger><TabsTrigger value="team">Team</TabsTrigger></TabsList>
        <TabsContent value="rules"><BusinessRules /></TabsContent>
        <TabsContent value="roles"><Roles /></TabsContent>
        <TabsContent value="team"><RequirePerm perm="users:read"><Team /></RequirePerm></TabsContent>
      </Tabs>
    </RequirePerm>
  );
}
