import type { Metadata } from "next";
import { ResellerCommissions } from "@/components/reseller/reseller-pages";

export const metadata: Metadata = { title: "Commissions" };

export default function Page() {
  return <ResellerCommissions />;
}
