"use client";

import * as React from "react";
import Link from "next/link";
import { Eye, Server as ServerIcon } from "lucide-react";
import { PageHeader } from "@/components/shell/page-header";
import { ResourceTable } from "@/components/resource/resource-table";
import { FormDialog, type FieldDef } from "@/components/resource/form-dialog";
import { RequirePerm, fmtDate } from "@/components/resource/helpers";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import type { Column, FilterDef } from "@/components/ui/data-table";
import { post } from "@/lib/http";
import { relTime } from "@/components/monitoring/format";
import { AvailabilityBadge } from "./availability-badge";
import { SecretReveal, enrollmentFields } from "./enrollment-reveal";
import type { Enrollment, ServerRow } from "./types";

const columns: Column<ServerRow>[] = [
  {
    id: "name",
    header: "Server",
    sortValue: (r) => r.name,
    cell: (r) => (
      <div>
        <Link href={`/admin/servers/${r.id}`} className="font-medium text-text hover:text-primary">
          {r.name}
        </Link>
        <p className="text-xs text-muted">{r.hostname ?? "hostname not reported"}</p>
      </div>
    ),
  },
  { id: "agent", header: "Agent", sortValue: (r) => r.availability, cell: (r) => <AvailabilityBadge value={r.availability} /> },
  { id: "version", header: "Version", hideOnMobile: true, sortValue: (r) => r.agent_version ?? "", cell: (r) => <span className="tnum">{r.agent_version ?? "—"}</span> },
  {
    id: "hb",
    header: "Last heartbeat",
    sortValue: (r) => r.last_heartbeat_at ?? "",
    cell: (r) => (
      <span title={fmtDate(r.last_heartbeat_at)} className="tnum">
        {relTime(r.last_heartbeat_at)}
      </span>
    ),
  },
  { id: "status", header: "Registration", hideOnMobile: true, sortValue: (r) => r.status, cell: (r) => <span className="capitalize">{r.status}</span> },
  { id: "registered", header: "Registered", hideOnMobile: true, defaultHidden: true, cell: (r) => fmtDate(r.registered_at) },
];

const filters: FilterDef<ServerRow>[] = [
  { id: "availability", label: "Agent states", options: ["connected", "stale", "disconnected", "unknown", "disabled"], match: (r, v) => r.availability === v },
];

const fields: FieldDef[] = [
  { name: "name", label: "Server name", required: true, hint: "Letters, numbers, spaces, dot, dash, underscore.", placeholder: "kvl-imac-1" },
  { name: "hostname", label: "Hostname", placeholder: "imac.example.com", hint: "Optional. The agent also reports its own hostname." },
];

export function ServersPage() {
  const { can } = useAuth();
  const [open, setOpen] = React.useState(false);
  const [enroll, setEnroll] = React.useState<Enrollment | null>(null);
  const [nonce, setNonce] = React.useState(0);

  return (
    <RequirePerm perm="servers:read">
      <PageHeader
        title="Servers"
        description="Register hosting servers and monitor their agents. A server stays Unknown until its agent has enrolled and sent a heartbeat."
        actions={
          <span className="hidden text-muted sm:inline-flex">
            <AnimatedIcon icon={ServerIcon} size={20} idle="float" />
          </span>
        }
      />
      <ResourceTable<ServerRow>
        key={nonce}
        path="/servers"
        caption="Servers"
        columns={columns}
        filters={filters}
        searchText={(r) => `${r.name} ${r.hostname ?? ""} ${r.agent_version ?? ""}`}
        refreshMs={30000}
        onCreate={() => setOpen(true)}
        createLabel="Register server"
        canCreate={can("servers:write")}
        emptyTitle="No servers registered"
        emptyDescription="Register your hosting server to get an enrollment token for its agent."
        rowActions={(r) => (
          <Button asChild size="sm" variant="ghost">
            <Link href={`/admin/servers/${r.id}`} aria-label={`Open ${r.name}`}>
              <Eye className="h-4 w-4" /> Open
            </Link>
          </Button>
        )}
      />
      <FormDialog
        open={open}
        onOpenChange={setOpen}
        title="Register server"
        description="Creates the server record and a single-use enrollment token (valid 24 hours)."
        fields={fields}
        submitLabel="Register"
        successMessage="Server registered"
        onSubmit={async (v) => {
          const res = await post<{ enrollment: Enrollment }>("/servers", v);
          setEnroll(res.enrollment);
          setNonce((n) => n + 1);
        }}
      />
      <SecretReveal
        open={enroll !== null}
        onClose={() => setEnroll(null)}
        title="Enroll the agent"
        description="Run the command on the server as the unprivileged kvl-agent user."
        fields={enroll ? enrollmentFields(enroll) : []}
      />
    </RequirePerm>
  );
}
