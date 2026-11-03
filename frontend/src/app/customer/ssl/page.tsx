import type { Metadata } from "next";
import { CustomerSslPage } from "@/components/ssl/ssl-dns-page";

export const metadata: Metadata = { title: "SSL" };

export default function Page() {
  return <CustomerSslPage />;
}
