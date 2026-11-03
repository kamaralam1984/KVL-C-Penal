import type { Metadata } from "next";
import { SupportConsole } from "@/components/support/support-console";

export const metadata: Metadata = { title: "Support" };

export default function Page() {
  return <SupportConsole />;
}
