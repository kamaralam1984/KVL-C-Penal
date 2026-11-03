import type { Metadata } from "next";
import { ApplicationsPage } from "@/components/applications/applications-page";

export const metadata: Metadata = { title: "Applications" };

export default function Page() {
  return <ApplicationsPage />;
}
