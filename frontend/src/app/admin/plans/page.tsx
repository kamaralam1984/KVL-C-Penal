import type { Metadata } from "next";
import { AdminPlans } from "@/components/billing/billing-pages";

export const metadata: Metadata = { title: "Plans" };

export default function Page() {
  return <AdminPlans />;
}
