import type { Metadata } from "next";
import { OpsCenterPage } from "@/components/dashboard/ops-center-page";

export const metadata: Metadata = { title: "Operations centre" };

export default function Page() {
  return <OpsCenterPage />;
}
