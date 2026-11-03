"use client";

import * as React from "react";
import { PageHeader } from "@/components/shell/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RequirePerm } from "@/components/resource/helpers";
import { useAuth } from "@/components/providers/auth-provider";
import { TicketList } from "./ticket-list";
import { KbPanel } from "./kb-panel";
import { AnnouncementsPanel } from "./announcements-panel";
import { ReportsPanel, StaffPanel } from "./staff-panel";

/** Agent console for /admin/support. Staff-only tabs render only for roles that hold tickets:manage. */
export function SupportConsole() {
  const { can } = useAuth();
  const staff = can("tickets:manage");
  return (
    <RequirePerm perm="tickets:read">
      <PageHeader title="Support" description="Ticket queue, SLAs, knowledge base, announcements and staff workload." />
      <Tabs defaultValue="inbox">
        <TabsList className="overflow-x-auto">
          <TabsTrigger value="inbox">Inbox</TabsTrigger>
          {staff && <TabsTrigger value="reports">Reports</TabsTrigger>}
          <TabsTrigger value="kb">Knowledge base</TabsTrigger>
          <TabsTrigger value="announcements">Announcements</TabsTrigger>
          {staff && <TabsTrigger value="staff">Staff &amp; workload</TabsTrigger>}
        </TabsList>
        <TabsContent value="inbox" className="pt-4"><TicketList tenantFilter /></TabsContent>
        {staff && <TabsContent value="reports" className="pt-4"><ReportsPanel /></TabsContent>}
        <TabsContent value="kb" className="pt-4"><KbPanel /></TabsContent>
        <TabsContent value="announcements" className="pt-4"><AnnouncementsPanel /></TabsContent>
        {staff && <TabsContent value="staff" className="pt-4"><StaffPanel /></TabsContent>}
      </Tabs>
    </RequirePerm>
  );
}

/** Customer help centre for /customer/support. */
export function CustomerSupport() {
  return (
    <RequirePerm perm="tickets:read">
      <PageHeader title="Support" description="Open a ticket, follow replies and search the help centre." />
      <Tabs defaultValue="tickets">
        <TabsList className="overflow-x-auto">
          <TabsTrigger value="tickets">My tickets</TabsTrigger>
          <TabsTrigger value="kb">Help articles</TabsTrigger>
          <TabsTrigger value="announcements">Announcements</TabsTrigger>
        </TabsList>
        <TabsContent value="tickets" className="pt-4"><TicketList /></TabsContent>
        <TabsContent value="kb" className="pt-4"><KbPanel /></TabsContent>
        <TabsContent value="announcements" className="pt-4"><AnnouncementsPanel /></TabsContent>
      </Tabs>
    </RequirePerm>
  );
}
