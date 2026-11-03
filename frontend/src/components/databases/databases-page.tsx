"use client";

import * as React from "react";
import { Database, Eye } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { ResourceTable } from "@/components/resource/resource-table";
import { RequirePerm, StatusPill, fmtBytes } from "@/components/resource/helpers";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import type { Column, FilterDef } from "@/components/ui/data-table";
import { CreateDbWizard } from "./create-wizard";
import { DatabaseDrawer } from "./database-drawer";
import { CredentialsModal } from "./secret-modal";
import { ENGINE_LABEL, type Credentials, type DbRow } from "./types";

const columns: Column<DbRow>[] = [
  { id: "name", header: "Database", sortValue: (r) => r.name, cell: (r) => (<div><p className="font-medium break-all text-text">{r.name}</p><p className="text-xs text-muted">{ENGINE_LABEL[r.engine]}</p></div>) },
  { id: "status", header: "Status", sortValue: (r) => r.status, cell: (r) => <StatusPill status={r.status} /> },
  { id: "size", header: "Size", hideOnMobile: true, sortValue: (r) => r.size_bytes ?? -1, cell: (r) => <span className="tnum">{fmtBytes(r.size_bytes)}</span> },
  { id: "host", header: "Host", hideOnMobile: true, cell: (r) => <span className="tnum text-xs">{r.host}:{r.port ?? "—"}</span> },
];
const filters: FilterDef<DbRow>[] = [{ id: "engine", label: "Engines", options: ["mariadb", "postgresql"], match: (r, v) => r.engine === v }];

export function DatabasesPage() {
  const { can } = useAuth();
  const [wizard, setWizard] = React.useState(false);
  const [open, setOpen] = React.useState<string | null>(null);
  const [nonce, setNonce] = React.useState(0);
  const [secret, setSecret] = React.useState<{ creds: Credentials; title: string } | null>(null);

  return (
    <RequirePerm perm="databases:read">
      <PageHeader
        title="Databases"
        description="Customer MariaDB and PostgreSQL databases, separate from the control plane. Passwords are shown once and never stored."
        actions={<span className="hidden text-muted sm:inline-flex"><AnimatedIcon icon={Database} size={20} idle="float" /></span>}
      />
      <ResourceTable<DbRow>
        key={nonce}
        path="/databases"
        caption="Databases"
        columns={columns}
        filters={filters}
        searchText={(r) => `${r.name} ${r.engine}`}
        refreshMs={10000}
        onCreate={() => setWizard(true)}
        createLabel="New database"
        canCreate={can("databases:write")}
        emptyTitle="No databases yet"
        emptyDescription="Create a database with a least-privilege user limited to that database."
        rowActions={(r) => (
          <Button size="sm" variant="ghost" aria-label={`Open ${r.name}`} onClick={() => setOpen(r.id)}>
            <Eye className="h-4 w-4" /> Open
          </Button>
        )}
      />
      <CreateDbWizard
        open={wizard}
        onOpenChange={setWizard}
        onCreated={(d) => {
          setNonce((n) => n + 1);
          setOpen(d.id);
          if (d.credentials) setSecret({ creds: d.credentials, title: "Database credentials" });
        }}
      />
      <DatabaseDrawer dbId={open} onClose={() => { setOpen(null); setNonce((n) => n + 1); }} onSecret={(creds, title) => setSecret({ creds, title })} />
      <CredentialsModal creds={secret?.creds ?? null} title={secret?.title ?? ""} onClose={() => setSecret(null)} />
    </RequirePerm>
  );
}
