import type { Metadata } from "next";
import { ServerDetail } from "@/components/servers/server-detail";

export const metadata: Metadata = { title: "Server" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ServerDetail id={id} />;
}
