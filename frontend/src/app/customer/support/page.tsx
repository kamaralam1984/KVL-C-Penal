import type { Metadata } from "next";
import { CustomerSupport } from "@/components/support/support-console";

export const metadata: Metadata = { title: "Support" };

export default function Page() {
  return <CustomerSupport />;
}
