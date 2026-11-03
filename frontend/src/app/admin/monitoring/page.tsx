import type { Metadata } from "next";
import { MonitoringDashboard } from "@/components/monitoring/monitoring-dashboard";

export const metadata: Metadata = { title: "Monitoring" };

export default function Page() {
  return <MonitoringDashboard />;
}
