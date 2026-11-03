"use client";

import Link from "next/link";
import { LayoutDashboard, LogIn } from "lucide-react";
import { Button } from "@/components/ui/button";
import { homeFor, useAuth } from "@/components/providers/auth-provider";

export function HomeCta() {
  const { me, loading } = useAuth();
  if (!loading && me)
    return (
      <Button asChild size="lg">
        <Link href={homeFor(me.role)}><LayoutDashboard className="h-4 w-4" /> Open dashboard</Link>
      </Button>
    );
  return (
    <Button asChild size="lg">
      <Link href="/login"><LogIn className="h-4 w-4" /> Sign in</Link>
    </Button>
  );
}
