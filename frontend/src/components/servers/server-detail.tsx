"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { DetailList, LiveBadge, RequirePerm, fmtDate } from "@/components/resource/helpers";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/states";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApi } from "@/hooks/use-api";
import { ServerMonitor } from "@/components/monitoring/server-monitor";
import { relTime } from "@/components/monitoring/format";
import { AvailabilityBadge, availabilityHint } from "./availability-badge";
import { CredentialsPanel } from "./credentials-panel";
import { JobsPanel } from "./jobs-panel";
import type { ServerRow } from "./types";

export function ServerDetail({ id }: { id: string }) {
  const { data, error, loading, reload } = useApi<ServerRow>(`/servers/${id}`, undefined, { refreshMs: 30000 });
  return (
    <RequirePerm perm="servers:read">
      <Button asChild variant="ghost" size="sm" className="mb-2">
        <Link href="/admin/servers"><ArrowLeft className="h-4 w-4" /> All servers</Link>
      </Button>
      {loading && <Skeleton className="h-40 w-full" />}
      {error && <ErrorState onRetry={reload} title={error.status === 404 ? "Server not found" : undefined} />}
      {data && (
        <>
          <PageHeader title={data.name} description={data.hostname ?? "Hostname not reported yet"} actions={<><AvailabilityBadge value={data.availability} /><LiveBadge /></>} />
          <Tabs defaultValue="overview" className="space-y-4">
            <TabsList className="overflow-x-auto">
              <TabsTrigger value="overview">Agent</TabsTrigger>
              <TabsTrigger value="monitoring">Monitoring</TabsTrigger>
              <TabsTrigger value="operations">Operations</TabsTrigger>
              <TabsTrigger value="credentials">Credentials</TabsTrigger>
            </TabsList>
            <TabsContent value="overview">
              <Card>
                <CardHeader title="Agent status" description={availabilityHint(data.availability)} />
                <CardBody>
                  <DetailList
                    items={[
                      ["State", <AvailabilityBadge key="a" value={data.availability} />],
                      ["Agent version", data.agent_version ?? "not reported"],
                      ["Last heartbeat", data.last_heartbeat_at ? `${fmtDate(data.last_heartbeat_at)} (${relTime(data.last_heartbeat_at)})` : "never"],
                      ["Registration", data.status],
                      ["Registered", fmtDate(data.registered_at)],
                      ["Active credential", data.credential_key_id ?? "none"],
                      ["Operating system", data.os_info ? `${String(data.os_info.system ?? "?")} ${String(data.os_info.release ?? "")}` : "not reported"],
                      ["Self-update", data.os_info?.update_status ? `${(data.os_info.update_status as { state?: string }).state ?? "unknown"} (reporting only)` : "not reported"],
                    ]}
                  />
                </CardBody>
              </Card>
            </TabsContent>
            <TabsContent value="monitoring"><ServerMonitor serverId={id} /></TabsContent>
            <TabsContent value="operations"><JobsPanel serverId={id} /></TabsContent>
            <TabsContent value="credentials"><CredentialsPanel server={data} onChanged={reload} /></TabsContent>
          </Tabs>
        </>
      )}
    </RequirePerm>
  );
}
