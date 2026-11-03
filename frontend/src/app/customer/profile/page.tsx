import type { Metadata } from "next";
import { PlaceholderPage } from "@/components/shell/placeholder-page";

export const metadata: Metadata = { title: "Profile" };

export default function Page() {
  return <PlaceholderPage role="customer" slug="profile" />;
}
