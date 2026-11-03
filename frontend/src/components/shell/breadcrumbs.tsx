"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronRight } from "lucide-react";
import { ROLES, findItem, type Role } from "@/config/nav";

export function Breadcrumbs({ role }: { role: Role }) {
  const pathname = usePathname();
  const cfg = ROLES[role];
  const slug = pathname.split("/")[2];
  const page = slug ? findItem(role, slug) : undefined;

  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="flex items-center gap-1.5 text-sm text-muted">
        <li className="hidden sm:block">
          <Link href={`${cfg.base}/dashboard`} className="rounded hover:text-text">
            {cfg.label}
          </Link>
        </li>
        {page && (
          <>
            <li aria-hidden className="hidden sm:block">
              <ChevronRight className="h-3.5 w-3.5 text-faint" />
            </li>
            <li aria-current="page" className="truncate font-medium text-text">
              {page.label}
            </li>
          </>
        )}
      </ol>
    </nav>
  );
}
