import type { Metadata } from "next";
import { AutomationPage } from "@/components/automation/automation-page";

export const metadata: Metadata = { title: "Automation" };

export default function Page() {
  return <AutomationPage />;
}
