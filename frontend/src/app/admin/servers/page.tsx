import type { Metadata } from "next";
import { ServersPage } from "@/components/servers/servers-page";

export const metadata: Metadata = { title: "Servers" };

export default function Page() {
  return <ServersPage />;
}
