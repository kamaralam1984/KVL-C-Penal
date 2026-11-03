import type { Metadata } from "next";
import { ResellerSettings } from "@/components/reseller/reseller-pages";

export const metadata: Metadata = { title: "Settings" };

export default function Page() {
  return <ResellerSettings />;
}
