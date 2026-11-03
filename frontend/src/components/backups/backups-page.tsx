"use client";

import * as React from "react";
import { Archive } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import { PermissionDenied } from "@/components/ui/states";
import { useAuth } from "@/components/providers/auth-provider";
import { useApi } from "@/hooks/use-api";
import { OverviewTab } from "./overview";
import { Inventory } from "./inventory";
import { SchedulesTab } from "./schedules";
import { RetentionTab } from "./retention";
import { RestoresTab, DestinationsTab, HistoryTab } from "./restores-dests-history";
import type { Ctx } from "./types";

export function BackupsPage() {
  const { me, can, loading } = useAuth();
  const platform = me?.scope === "platform";
  const [tid, setTid] = React.useState("");
  const tenants = useApi<{ items: { id: string; name: string }[] }>(platform ? "/tenants" : null, { limit: 100 });
  const ctx = React.useMemo<Ctx>(() => {
    const t = platform && tid ? tid : null;
    return { tid: t, q: (t ? { tenant_id: t } : {}) as Record<string, string>, body: (b) => (t ? { ...b, tenant_id: t } : b), needTenant: platform === true && !t };
  }, [platform, tid]);
  if (loading) return null;
  if (!can("backups:read")) return <Card><PermissionDenied /></Card>;
  return (
    <>
      <PageHeader
        title="Backups & recovery"
        description="Backups, schedules, retention and guarded restores."
        actions={
          <>
            <AnimatedIcon icon={Archive} idle="float" size={22} className="text-primary" />
            {platform && (
              <Select aria-label="Tenant" className="min-h-11 w-56" value={tid} onChange={(e) => setTid(e.target.value)}>
                <option value="">All tenants (read-only)</option>
                {tenants.data?.items.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </Select>
            )}
          </>
        }
      />
      {ctx.needTenant && <p className="mb-3 rounded-md bg-info/12 p-3 text-sm text-info">Select a tenant to create backups, schedules or policies and to run simulations.</p>}
      <Tabs defaultValue="overview">
        <div className="overflow-x-auto"><TabsList className="min-w-max">
          {["overview", "inventory", "schedules", "retention", "restores", "destinations", "history"].map((t) => <TabsTrigger key={t} value={t} className="min-h-11 capitalize">{t}</TabsTrigger>)}
        </TabsList></div>
        <div className="pt-4">
          <TabsContent value="overview"><OverviewTab ctx={ctx} /></TabsContent>
          <TabsContent value="inventory"><Inventory ctx={ctx} /></TabsContent>
          <TabsContent value="schedules"><SchedulesTab ctx={ctx} /></TabsContent>
          <TabsContent value="retention"><RetentionTab ctx={ctx} /></TabsContent>
          <TabsContent value="restores"><RestoresTab ctx={ctx} /></TabsContent>
          <TabsContent value="destinations"><DestinationsTab ctx={ctx} /></TabsContent>
          <TabsContent value="history"><HistoryTab ctx={ctx} /></TabsContent>
        </div>
      </Tabs>
    </>
  );
}
