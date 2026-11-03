import type { Metadata } from "next";
import { CustomerBilling } from "@/components/billing/billing-pages";

export const metadata: Metadata = { title: "Billing" };

export default function Page() {
  return <CustomerBilling />;
}
