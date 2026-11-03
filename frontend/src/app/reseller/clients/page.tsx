import type { Metadata } from "next";
import { ResellerClients } from "@/components/reseller/reseller-pages";

export const metadata: Metadata = { title: "Clients" };

export default function Page() {
  return <ResellerClients />;
}
