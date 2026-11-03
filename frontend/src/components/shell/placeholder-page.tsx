import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "./page-header";
import { Card } from "@/components/ui/card";
import { NotYetAvailable } from "@/components/ui/states";
import { Button } from "@/components/ui/button";
import { ROLES, findItem, type Role } from "@/config/nav";

/** Route stub for modules built in later phases. Says so plainly: no fake data, no dead controls. */
export function PlaceholderPage({ role, slug }: { role: Role; slug: string }) {
  const it = findItem(role, slug);
  if (!it) notFound();
  const cfg = ROLES[role];
  return (
    <>
      <PageHeader title={it.label} description={it.description} />
      <Card>
        <NotYetAvailable
          feature={it.label}
          phase={it.phase}
          action={
            <Button asChild variant="outline" size="sm">
              <Link href={`${cfg.base}/dashboard`}>
                <ArrowLeft className="h-3.5 w-3.5" /> Back to dashboard
              </Link>
            </Button>
          }
        />
      </Card>
    </>
  );
}
