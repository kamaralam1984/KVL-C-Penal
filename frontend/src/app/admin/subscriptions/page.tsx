import type { Metadata } from "next";
import { AdminSubscriptions } from "@/components/billing/billing-pages";

export const metadata: Metadata = { title: "Subscriptions" };

export default function Page() {
  return <AdminSubscriptions />;
}
