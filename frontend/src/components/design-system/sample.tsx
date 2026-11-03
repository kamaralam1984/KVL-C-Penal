"use client";

import { Eye, MoreHorizontal } from "lucide-react";
import { DataTable, type Column, type FilterDef } from "@/components/ui/data-table";
import { Badge, type Tone } from "@/components/ui/badge";
import { IconButton } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { useToast } from "@/components/ui/toast";

/** Sample content for the component showcase only. It is never shown inside the product. */
interface Row { id: string; domain: string; owner: string; runtime: string; status: "Active" | "Pending" | "Suspended"; diskMb: number }

const owners = ["Northwind", "Acme Retail", "Lotus Dental", "Harbor Cafe"];
const runtimes = ["PHP 8.3", "Node 22", "Python 3.12", "Static"];
const statuses: Row["status"][] = ["Active", "Active", "Pending", "Suspended"];
export const sampleRows: Row[] = Array.from({ length: 14 }, (_, i) => ({
  id: `s${i}`,
  domain: `sample-site-${String(i + 1).padStart(2, "0")}.test`,
  owner: owners[i % owners.length],
  runtime: runtimes[(i * 3) % runtimes.length],
  status: statuses[(i * 7) % statuses.length],
  diskMb: 120 + ((i * 937) % 4800),
}));

export const sampleSeries = Array.from({ length: 14 }, (_, i) => ({
  t: `${String(i + 1).padStart(2, "0")} Jan`,
  subscriptions: Math.round(28 + 4 * Math.sin(i / 2.3) + 0.9 * i),
  addons: Math.round(9 + 2 * Math.sin((i + 3) / 2.3) + 0.3 * i),
}));

const tone: Record<Row["status"], Tone> = { Active: "success", Pending: "info", Suspended: "neutral" };
const columns: Column<Row>[] = [
  { id: "domain", header: "Domain", sortValue: (r) => r.domain, cell: (r) => <span className="font-medium text-text">{r.domain}</span> },
  { id: "owner", header: "Owner", hideOnMobile: true, sortValue: (r) => r.owner, cell: (r) => r.owner },
  { id: "runtime", header: "Runtime", hideOnMobile: true, sortValue: (r) => r.runtime, cell: (r) => <span className="font-mono text-sm">{r.runtime}</span> },
  { id: "status", header: "Status", sortValue: (r) => r.status, cell: (r) => <Badge tone={tone[r.status]} dot>{r.status}</Badge> },
  { id: "disk", header: "Disk", align: "right", sortValue: (r) => r.diskMb, cell: (r) => `${r.diskMb} MB` },
];
const filters: FilterDef<Row>[] = [{ id: "status", label: "Statuses", options: ["Active", "Pending", "Suspended"], match: (r, v) => r.status === v }];

export function SampleTable() {
  const toast = useToast();
  return (
    <DataTable
      demo
      caption="Sample websites"
      rows={sampleRows}
      columns={columns}
      filters={filters}
      searchText={(r) => `${r.domain} ${r.owner}`}
      rowActions={(r) => (
        <Menu>
          <MenuTrigger asChild><IconButton label={`Actions for ${r.domain}`}><MoreHorizontal className="h-4 w-4" /></IconButton></MenuTrigger>
          <MenuContent><MenuItem icon={Eye} onSelect={() => toast({ title: "Sample action", description: "Showcase only." })}>View details</MenuItem></MenuContent>
        </Menu>
      )}
    />
  );
}
