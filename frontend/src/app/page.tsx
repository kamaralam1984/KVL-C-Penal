import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Brand } from "@/components/shell/brand";
import { ThemeSelector } from "@/components/shell/theme-selector";
import { AccentLine } from "@/components/ui/accent-line";
import { Button } from "@/components/ui/button";
import { FeatureGrid } from "@/components/auth/feature-grid";
import { HomeCta } from "@/components/auth/home-cta";

export default function Home() {
  return (
    <div className="mx-auto min-h-dvh max-w-5xl px-5 pt-[calc(var(--safe-top)+1.25rem)] pb-10 sm:px-6">
      <header className="flex items-center justify-between">
        <Brand />
        <ThemeSelector />
      </header>
      <main id="main-content" className="py-14 sm:py-20">
        <div className="w-24"><AccentLine /></div>
        <h1 className="mt-5 max-w-2xl text-4xl font-semibold tracking-tight text-text sm:text-5xl">Control panel for KVL private hosting</h1>
        <p className="mt-4 max-w-2xl text-lg text-muted">Manage servers, websites, domains, databases, backups and billing from one fast, secure panel built for desktop and mobile.</p>
        <div className="mt-8 flex flex-wrap gap-3">
          <HomeCta />
          <Button asChild variant="outline" size="lg"><Link href="/register">Create account <ArrowRight className="h-4 w-4" /></Link></Button>
        </div>
        <FeatureGrid />
        <p className="mt-10 text-sm text-muted"><Link href="/design-system" className="font-medium text-primary hover:underline">Design system</Link></p>
      </main>
    </div>
  );
}
