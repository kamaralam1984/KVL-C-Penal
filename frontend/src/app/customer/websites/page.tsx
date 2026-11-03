import type { Metadata } from "next";
import { WebsitesPage } from "@/components/websites/websites-page";

export const metadata: Metadata = { title: "Websites" };

export default function Page() {
  return <WebsitesPage />;
}
