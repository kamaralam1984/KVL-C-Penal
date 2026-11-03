"use client";

import * as React from "react";
import { Boxes, Eye } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { ResourceTable } from "@/components/resource/resource-table";
import { RequirePerm, StatusPill } from "@/components/resource/helpers";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import type { Column, FilterDef } from "@/components/ui/data-table";
import { CreateAppWizard } from "./create-wizard";
import { AppDrawer } from "./app-drawer";
import { RUNTIME_LABEL, type AppRow } from "./types";

const columns: Column<AppRow>[] = [
  {
    id: "name",
    header: "Application",
    sortValue: (r) => r.name,
    cell: (r) => (
      <div>
        <p className="font-medium text-text">{r.name}</p>
        <p className="text-xs text-muted">{RUNTIME_LABEL[r.runtime] ?? r.runtime}</p>
      </div>
    ),
  },
  { id: "version", header: "Version", sortValue: (r) => r.runtime_version, cell: (r) => <span className="tnum">{r.runtime_version}</span> },
  { id: "status", header: "Status", sortValue: (r) => r.status, cell: (r) => <StatusPill status={r.status} /> },
  { id: "state", header: "Deployment", hideOnMobile: true, sortValue: (r) => r.provision_state, cell: (r) => <StatusPill status={r.provision_state} /> },
  { id: "release", header: "Release", hideOnMobile: true, cell: (r) => <span className="tnum text-xs">{r.current_release ?? "—"}</span> },
];

const filters: FilterDef<AppRow>[] = [{ id: "runtime", label: "Runtimes", options: ["php", "wordpress", "nodejs", "python"], match: (r, v) => r.runtime === v }];

export function ApplicationsPage() {
  const { can } = useAuth();
  const [wizard, setWizard] = React.useState(false);
  const [open, setOpen] = React.useState<string | null>(null);
  const [nonce, setNonce] = React.useState(0);

  return (
    <RequirePerm perm="apps:read">
      <PageHeader
        title="Applications"
        description="PHP, WordPress, Node.js and Python apps with atomic releases, health-checked deployments and one-click rollback."
        actions={
          <span className="hidden text-muted sm:inline-flex">
            <AnimatedIcon icon={Boxes} size={20} idle="float" />
          </span>
        }
      />
      <ResourceTable<AppRow>
        key={nonce}
        path="/apps"
        caption="Applications"
        columns={columns}
        filters={filters}
        searchText={(r) => `${r.name} ${r.runtime} ${r.runtime_version}`}
        refreshMs={15000}
        onCreate={() => setWizard(true)}
        createLabel="New application"
        canCreate={can("apps:write")}
        emptyTitle="No applications yet"
        emptyDescription="Create an application, pick an installed runtime and connect a Git repository."
        rowActions={(r) => (
          <Button size="sm" variant="ghost" onClick={() => setOpen(r.id)} aria-label={`Open ${r.name}`}>
            <Eye className="h-4 w-4" /> Open
          </Button>
        )}
      />
      <CreateAppWizard
        open={wizard}
        onOpenChange={setWizard}
        onCreated={(a) => {
          setNonce((n) => n + 1);
          setOpen(a.id);
        }}
      />
      <AppDrawer appId={open} onClose={() => { setOpen(null); setNonce((n) => n + 1); }} />
    </RequirePerm>
  );
}
