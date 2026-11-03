import type { Metadata } from "next";
import { AdminResellers } from "@/components/reseller/reseller-pages";

export const metadata: Metadata = { title: "Resellers" };

export default function Page() {
  return <AdminResellers />;
}
