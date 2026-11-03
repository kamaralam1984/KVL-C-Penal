import type { Metadata } from "next";
import { ResellerReports } from "@/components/reseller/reseller-pages";

export const metadata: Metadata = { title: "Reports" };

export default function Page() {
  return <ResellerReports />;
}
