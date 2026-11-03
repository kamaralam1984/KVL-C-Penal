import type { Metadata } from "next";
import { CustomerDashboard } from "@/components/dashboard/live-dashboards";

export const metadata: Metadata = { title: "Dashboard" };

export default function Page() {
  return <CustomerDashboard />;
}
