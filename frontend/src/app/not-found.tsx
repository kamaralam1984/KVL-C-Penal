import Link from "next/link";
import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/shell/brand";

export default function NotFound() {
  return (
    <main id="main-content" className="grid min-h-screen place-items-center px-6 text-center">
      <div>
        <BrandMark className="mx-auto mb-4 h-10 w-10" />
        <p className="tnum text-sm font-medium text-faint">404</p>
        <h1 className="mt-1 text-2xl font-semibold text-text">Page not found</h1>
        <p className="mt-1 text-muted">That address doesn&apos;t exist in the panel.</p>
        <Button asChild className="mt-5">
          <Link href="/">Back to start</Link>
        </Button>
      </div>
    </main>
  );
}
