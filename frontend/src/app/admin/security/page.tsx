import type { Metadata } from "next";
import { SecurityConsole } from "@/components/security/security-console";

export const metadata: Metadata = { title: "Security" };

export default function Page() {
  return <SecurityConsole />;
}
