"use client";

import { ThemeProvider as NextThemes } from "next-themes";

// Light is the default; "system" is opt-in via the theme selector.
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemes attribute="class" defaultTheme="light" enableSystem disableTransitionOnChange>
      {children}
    </NextThemes>
  );
}
