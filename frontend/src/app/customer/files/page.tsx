import type { Metadata } from "next";
import { FilesPage } from "@/components/files/files-page";

export const metadata: Metadata = { title: "Files" };

export default function Page() {
  return <FilesPage />;
}
