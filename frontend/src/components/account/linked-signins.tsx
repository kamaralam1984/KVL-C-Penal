"use client";

import * as React from "react";
import { Link2 } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useApi } from "@/hooks/use-api";
import { fmtDate } from "@/components/resource/helpers";
import { ApiError, del, post } from "@/lib/http";

interface Identity { provider: string; email: string | null; created_at: string; last_used_at: string | null }

/** Link Google / Microsoft sign-in to this account (required before Microsoft sign-in can be used). */
export function LinkedSignIns() {
  const toast = useToast();
  const providers = useApi<{ id: string; label: string }[]>("/auth/sso/providers");
  const ids = useApi<Identity[]>("/auth/sso/identities");
  React.useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("sso");
    if (q === "linked") toast({ title: "Sign-in linked", tone: "success" });
    if (q === "in_use") toast({ title: "That sign-in is already linked to another account", tone: "danger" });
  }, [toast]);
  if (!providers.data?.length) return null;
  return (
    <Card>
      <CardHeader title="Sign in with Google or Microsoft" description="Link an account to sign in without your password. Two-factor authentication still applies." actions={<Link2 className="h-5 w-5 text-primary" aria-hidden />} />
      <CardBody>
        <ul className="divide-y divide-border">
          {providers.data.map((p) => {
            const linked = ids.data?.find((i) => i.provider === p.id);
            return (
              <li key={p.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                <div><p className="font-medium text-text">{p.label}</p><p className="text-xs text-muted">{linked ? `${linked.email ?? "linked"} · last used ${linked.last_used_at ? fmtDate(linked.last_used_at) : "never"}` : "Not linked"}</p></div>
                {linked ? (
                  <Button size="sm" variant="outline" onClick={async () => { try { await del(`/auth/sso/identities/${p.id}`); toast({ title: `${p.label} unlinked`, tone: "success" }); ids.reload(); } catch (e) { toast({ title: "Could not unlink", description: e instanceof ApiError ? e.message : undefined, tone: "danger" }); } }}>Unlink</Button>
                ) : (
                  <Button size="sm" onClick={async () => { try { const r = await post<{ url: string }>(`/auth/sso/${p.id}/start`, { mode: "link" }); window.location.assign(r.url); } catch (e) { toast({ title: "Could not start linking", description: e instanceof ApiError ? e.message : undefined, tone: "danger" }); } }}>Link {p.label}</Button>
                )}
              </li>
            );
          })}
        </ul>
      </CardBody>
    </Card>
  );
}
