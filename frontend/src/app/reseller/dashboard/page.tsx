import type { Metadata } from "next";
import { ResellerDashboard } from "@/components/reseller/reseller-pages";

export const metadata: Metadata = { title: "Reseller dashboard" };

export default function Page() {
  return <ResellerDashboard />;
}
