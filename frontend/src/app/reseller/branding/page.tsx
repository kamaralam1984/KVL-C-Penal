import type { Metadata } from "next";
import { ResellerBranding } from "@/components/reseller/reseller-pages";

export const metadata: Metadata = { title: "Branding" };

export default function Page() {
  return <ResellerBranding />;
}
