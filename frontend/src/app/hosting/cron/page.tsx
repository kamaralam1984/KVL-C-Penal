import type { Metadata } from "next";
import { CronPage } from "@/components/cron/cron-page";

export const metadata: Metadata = { title: "Cron jobs" };

export default function Page() {
  return <CronPage />;
}
