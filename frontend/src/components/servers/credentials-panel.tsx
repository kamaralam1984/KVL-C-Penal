"use client";

import * as React from "react";
import { KeyRound, Power, RotateCw, ShieldOff, Ticket } from "lucide-react";
import { Can, StatusPill, fmtDate } from "@/components/resource/helpers";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { useApi } from "@/hooks/use-api";
import { ApiError, post } from "@/lib/http";
import { relTime } from "@/components/monitoring/format";
import { SecretReveal, enrollmentFields } from "./enrollment-reveal";
import type { CredentialRow, Enrollment, ServerRow } from "./types";

type Action = "rotate" | "revoke" | "disable" | "enable" | "token";
type Reveal = { title: string; description: string; fields: { label: string; value: string }[] } | null;

const COPY: Record<Action, { title: string; description: string; label: string; destructive: boolean }> = {
  rotate: { title: "Rotate agent credentials?", description: "Issues a new key and revokes the current one immediately. The agent stops working until it is configured with the new secret.", label: "Rotate", destructive: true },
  revoke: { title: "Revoke agent credentials?", description: "The agent is rejected on its next request. Rotate or re-enroll to restore access.", label: "Revoke", destructive: true },
  disable: { title: "Disable this server?", description: "Revokes credentials and blocks new operations. You can enable it again later.", label: "Disable", destructive: true },
  enable: { title: "Enable this server?", description: "Credentials stay revoked; rotate to issue a new one.", label: "Enable", destructive: false },
  token: { title: "Issue a new enrollment token?", description: "Any unused token is voided. The token is valid for 24 hours and works once.", label: "Issue token", destructive: false },
};

export function CredentialsPanel({ server, onChanged }: { server: ServerRow; onChanged: () => void }) {
  const toast = useToast();
  const creds = useApi<CredentialRow[]>(`/servers/${server.id}/credentials`);
  const [action, setAction] = React.useState<Action | null>(null);
  const [reveal, setReveal] = React.useState<Reveal>(null);

  async function perform(a: Action) {
    try {
      if (a === "rotate") {
        const r = await post<{ key_id: string; secret: string }>(`/servers/${server.id}/credentials/rotate`);
        setReveal({ title: "New agent credentials", description: "Configure the agent with these values.", fields: [{ label: "Key ID", value: r.key_id }, { label: "Secret", value: r.secret }] });
      } else if (a === "token") {
        const r = await post<Enrollment>(`/servers/${server.id}/enrollment-token`);
        setReveal({ title: "Enrollment token", description: "Run the command on the server.", fields: enrollmentFields(r) });
      } else {
        await post(`/servers/${server.id}/${a === "revoke" ? "credentials/revoke" : a}`);
        toast({ title: `Server ${a === "revoke" ? "credentials revoked" : a + "d"}`, tone: "success" });
      }
      creds.reload();
      onChanged();
    } catch (e) {
      toast({ title: "Action failed", description: e instanceof ApiError ? e.message : "Network error", tone: "danger" });
    }
  }

  const disabled = server.status === "disabled";
  return (
    <Card>
      <CardHeader
        title="Agent credentials"
        description="HMAC credentials the agent signs every request with. Secrets are shown once and stored encrypted."
        actions={
          <Can perm="servers:write">
            <Button size="sm" variant="outline" onClick={() => setAction("token")} disabled={disabled}>
              <Ticket className="h-4 w-4" /> New enrollment token
            </Button>
          </Can>
        }
      />
      <CardBody className="space-y-4">
        {creds.loading && <Skeleton className="h-20 w-full" />}
        {creds.error && <ErrorState onRetry={creds.reload} />}
        {creds.data && creds.data.length === 0 && <EmptyState title="No credentials issued" description="The agent has not enrolled yet. Issue an enrollment token and run the command on the server." />}
        {creds.data && creds.data.length > 0 && (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full text-sm">
              <caption className="sr-only">Agent credentials</caption>
              <thead className="bg-subtle text-left text-xs text-muted">
                <tr><th className="px-3 py-2">Key ID</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Created</th><th className="px-3 py-2">Last used</th></tr>
              </thead>
              <tbody className="divide-y divide-border">
                {creds.data.map((c) => (
                  <tr key={c.id}>
                    <td className="px-3 py-2"><code className="text-xs">{c.key_id}</code></td>
                    <td className="px-3 py-2"><StatusPill status={c.status} /></td>
                    <td className="tnum px-3 py-2">{fmtDate(c.created_at)}</td>
                    <td className="tnum px-3 py-2">{c.last_used_at ? relTime(c.last_used_at) : "never"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Can perm="servers:manage" fallback={<p className="text-xs text-muted">Rotating, revoking and disabling require the servers:manage permission.</p>}>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => setAction("rotate")} disabled={disabled}><RotateCw className="h-4 w-4" /> Rotate</Button>
            <Button variant="outline" onClick={() => setAction("revoke")} disabled={!server.has_active_credential}><ShieldOff className="h-4 w-4" /> Revoke</Button>
            {disabled ? (
              <Button variant="outline" onClick={() => setAction("enable")}><Power className="h-4 w-4" /> Enable server</Button>
            ) : (
              <Button variant="danger" onClick={() => setAction("disable")}><KeyRound className="h-4 w-4" /> Disable server</Button>
            )}
          </div>
        </Can>
      </CardBody>
      {action && (
        <ConfirmDialog
          open
          onOpenChange={(o) => !o && setAction(null)}
          title={COPY[action].title}
          description={COPY[action].description}
          confirmLabel={COPY[action].label}
          destructive={COPY[action].destructive}
          onConfirm={() => void perform(action)}
        />
      )}
      <SecretReveal open={reveal !== null} onClose={() => setReveal(null)} title={reveal?.title ?? ""} description={reveal?.description ?? ""} fields={reveal?.fields ?? []} />
    </Card>
  );
}
