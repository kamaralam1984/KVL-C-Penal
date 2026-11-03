import type { Metadata } from "next";
import { TenantsPage } from "@/components/tenants/tenants-page";

export const metadata: Metadata = { title: "Customers" };

export default function Page() {
  return <TenantsPage />;
}
