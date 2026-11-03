"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApi } from "@/hooks/use-api";
import { AlertsPanel } from "./alerts-panel";
import { LiveResources } from "./live-resources";
import { MetricsPanel } from "./metrics-panel";
import { SitesUsage } from "./sites-usage";
import { ServicesList } from "./services-list";
import { ThresholdsEditor } from "./thresholds-editor";
import type { ThresholdRow } from "./types";

/** Everything monitoring knows about one server. Shared by /admin/monitoring and /admin/servers/[id]. */
export function ServerMonitor({ serverId }: { serverId: string }) {
  const th = useApi<ThresholdRow[]>("/monitoring/thresholds", { server_id: serverId });
  const map = Object.fromEntries((th.data ?? []).filter((t) => t.enabled && t.unit === "%").map((t) => [t.metric, { warning: t.warning, critical: t.critical }]));
  return (
    <Tabs defaultValue="live" className="space-y-4">
      <TabsList className="overflow-x-auto">
        <TabsTrigger value="live">Live</TabsTrigger>
        <TabsTrigger value="sites">Websites</TabsTrigger>
        <TabsTrigger value="metrics">History</TabsTrigger>
        <TabsTrigger value="services">Services</TabsTrigger>
        <TabsTrigger value="alerts">Alerts</TabsTrigger>
        <TabsTrigger value="thresholds">Thresholds</TabsTrigger>
      </TabsList>
      <TabsContent value="live"><LiveResources serverId={serverId} thresholds={map} /></TabsContent>
      <TabsContent value="sites"><SitesUsage serverId={serverId} /></TabsContent>
      <TabsContent value="metrics"><MetricsPanel serverId={serverId} thresholds={map} /></TabsContent>
      <TabsContent value="services"><ServicesList serverId={serverId} /></TabsContent>
      <TabsContent value="alerts"><AlertsPanel serverId={serverId} showServer={false} /></TabsContent>
      <TabsContent value="thresholds"><ThresholdsEditor serverId={serverId} /></TabsContent>
    </Tabs>
  );
}
