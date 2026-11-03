import type { Metadata } from "next";
import { AdminDashboard } from "@/components/dashboard/live-dashboards";

export const metadata: Metadata = { title: "Dashboard" };

export default function Page() {
  return <AdminDashboard />;
}
