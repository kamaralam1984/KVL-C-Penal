"use client";

import { ErrorState } from "@/components/ui/states";

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main id="main-content" className="grid min-h-screen place-items-center">
      <ErrorState onRetry={reset} />
    </main>
  );
}
