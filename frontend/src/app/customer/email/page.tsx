import type { Metadata } from "next";
import { EmailSetupPage } from "@/components/email/email-setup-page";

export const metadata: Metadata = { title: "Email" };

export default function Page() {
  return <EmailSetupPage />;
}
