import type { Metadata } from "next";
import { BackupsPage } from "@/components/backups/backups-page";

export const metadata: Metadata = { title: "Backups" };

export default function Page() {
  return <BackupsPage />;
}
