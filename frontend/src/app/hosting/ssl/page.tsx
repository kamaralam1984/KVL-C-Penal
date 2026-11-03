import type { Metadata } from "next";
import { SslDnsPage } from "@/components/ssl/ssl-dns-page";

export const metadata: Metadata = { title: "SSL & DNS" };

export default function Page() {
  return <SslDnsPage />;
}
