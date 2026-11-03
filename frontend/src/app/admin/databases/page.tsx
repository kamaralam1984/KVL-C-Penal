import type { Metadata } from "next";
import { DatabasesPage } from "@/components/databases/databases-page";

export const metadata: Metadata = { title: "Databases" };

export default function Page() {
  return <DatabasesPage />;
}
