import type { Metadata } from "next";
import { ResellerPackages } from "@/components/reseller/reseller-pages";

export const metadata: Metadata = { title: "Packages" };

export default function Page() {
  return <ResellerPackages />;
}
