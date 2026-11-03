import type { Metadata } from "next";
import { AdminBilling } from "@/components/billing/billing-pages";

export const metadata: Metadata = { title: "Billing" };

export default function Page() {
  return <AdminBilling />;
}
