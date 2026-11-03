import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Brand } from "@/components/shell/brand";
import { ThemeSelector } from "@/components/shell/theme-selector";
import { PageHeader } from "@/components/shell/page-header";
import { DesignSystemDemo } from "@/components/design-system/demo";

export const metadata: Metadata = { title: "Design system" };

export default function DesignSystemPage() {
  return (
    <div className="mx-auto min-h-screen max-w-5xl px-4 py-6 sm:px-6">
      <header className="mb-8 flex items-center justify-between">
        <Link href="/" aria-label="Back to start" className="flex items-center gap-3 rounded-md">
          <ArrowLeft className="h-4 w-4 text-muted" aria-hidden />
          <Brand />
        </Link>
        <ThemeSelector />
      </header>
      <main id="main-content">
        <PageHeader
          title="Design system"
          description="KVL tokens and reusable components. Light is the default theme; dark is complete. Toggle the theme to check both."
        />
        <DesignSystemDemo />
      </main>
    </div>
  );
}
