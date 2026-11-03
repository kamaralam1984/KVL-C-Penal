"use client";

import * as React from "react";
import { Bell, Download, Plus, Server, Trash2 } from "lucide-react";
import { Button, IconButton } from "@/components/ui/button";
import { Badge, DemoBadge, StatusBadge, type Tone } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Field, Input, SearchInput, Select } from "@/components/ui/input";
import { Segmented, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConfirmDialog, Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Tooltip } from "@/components/ui/tooltip";
import { useToast } from "@/components/ui/toast";
import { EmptyState, ErrorState, NotYetAvailable, PermissionDenied } from "@/components/ui/states";
import { CardSkeleton, TableSkeleton } from "@/components/ui/skeleton";
import { KpiCard } from "@/components/ui/kpi-card";
import { DateRangeSelector, type RangeKey } from "@/components/ui/date-range";
import { TrendChart } from "@/components/charts/trend-chart";
import { SampleTable, sampleSeries } from "./sample";

const swatches: { name: string; token: string }[] = [
  { name: "Background", token: "bg" },
  { name: "Surface", token: "surface" },
  { name: "Elevated", token: "elevated" },
  { name: "Subtle", token: "subtle" },
  { name: "Border", token: "border" },
  { name: "Text", token: "text" },
  { name: "Muted", token: "muted" },
  { name: "Primary", token: "primary" },
  { name: "Primary soft", token: "primary-soft" },
  { name: "Secondary", token: "secondary" },
  { name: "Accent", token: "accent" },
  { name: "Success", token: "success" },
  { name: "Warning", token: "warning" },
  { name: "Danger", token: "danger" },
  { name: "Info", token: "info" },
];

const type = [
  ["text-3xl font-semibold tracking-tight", "Display 30 · Page KPI value"],
  ["text-2xl font-semibold tracking-tight", "Heading 24 · Page title"],
  ["text-xl font-semibold", "Heading 20 · Dialog title"],
  ["text-lg font-semibold", "Heading 16 · Card title"],
  ["text-base", "Body 14 · Default interface text"],
  ["text-sm text-muted", "Small 13 · Descriptions and meta"],
  ["text-xs text-faint", "Caption 12 · Hints and timestamps"],
] as const;

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} className="scroll-mt-20">
      <h2 id={id} className="mb-3 text-xl font-semibold tracking-tight text-text">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function DesignSystemDemo() {
  const toast = useToast();
  const [seg, setSeg] = React.useState<"grid" | "list">("list");
  const [range, setRange] = React.useState<RangeKey>("7d");
  const [q, setQ] = React.useState("");
  const [confirm, setConfirm] = React.useState(false);

  return (
    <div className="space-y-10">
      <Section id="color" title="Colour tokens">
        <p className="mb-3 text-sm text-muted">Semantic tokens switch automatically with the theme. Use the theme selector to compare.</p>
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {swatches.map((s) => (
            <li key={s.token} className="overflow-hidden rounded-lg border border-border bg-surface">
              <div className="h-12 border-b border-border" style={{ background: `var(--${s.token})` }} />
              <div className="px-2.5 py-2">
                <p className="text-sm font-medium text-text">{s.name}</p>
                <p className="font-mono text-xs text-muted">--{s.token}</p>
              </div>
            </li>
          ))}
        </ul>
        <p className="mt-4 mb-2 text-sm font-medium text-text">Chart palette</p>
        <div className="flex gap-2">
          {[1, 2, 3, 4, 5].map((n) => (
            <div key={n} className="h-8 flex-1 rounded-md" style={{ background: `var(--chart-${n})` }} title={`--chart-${n}`} />
          ))}
        </div>
      </Section>

      <Section id="type" title="Typography">
        <Card className="divide-y divide-border">
          {type.map(([cls, sample]) => (
            <div key={sample} className="flex flex-wrap items-baseline justify-between gap-2 px-5 py-3">
              <span className={cls}>{sample}</span>
              <code className="font-mono text-xs text-faint">{cls.split(" ")[0]}</code>
            </div>
          ))}
          <div className="px-5 py-3">
            <p className="mb-1 text-xs text-faint">Data typography: tabular numerals keep columns aligned</p>
            <p className="tnum font-mono text-base text-text">1,234.50 · 0,987.25 · 12,345.00</p>
          </div>
        </Card>
      </Section>

      <Section id="buttons" title="Buttons, badges and status">
        <Card className="space-y-5 p-5">
          <div className="flex flex-wrap items-center gap-2">
            <Button>Primary</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="outline">Outline</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger">
              <Trash2 className="h-4 w-4" /> Danger
            </Button>
            <Button loading>Saving</Button>
            <Button disabled>Disabled</Button>
            <Button size="sm">Small</Button>
            <Button size="lg">Large</Button>
            <IconButton label="Download" variant="outline">
              <Download className="h-4 w-4" />
            </IconButton>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {(["neutral", "primary", "success", "warning", "danger", "info"] as Tone[]).map((t) => (
              <Badge key={t} tone={t} dot>
                {t}
              </Badge>
            ))}
            <DemoBadge />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status="healthy" />
            <StatusBadge status="warning" />
            <StatusBadge status="critical" />
            <StatusBadge status="pending" />
            <StatusBadge status="suspended" />
            <StatusBadge status="unknown" label="Not configured" />
          </div>
        </Card>
      </Section>

      <Section id="forms" title="Form fields and filters">
        <Card className="grid gap-4 p-5 sm:grid-cols-2">
          <Field label="Domain name" required hint="Lowercase, without protocol.">
            {(p) => <Input placeholder="example.com" {...p} />}
          </Field>
          <Field label="Runtime" hint="Only installed versions are listed.">
            {(p) => (
              <Select {...p}>
                <option>PHP 8.3</option>
                <option>Node 22</option>
              </Select>
            )}
          </Field>
          <Field label="Admin email" error="Enter a valid email address.">
            {(p) => <Input defaultValue="not-an-email" {...p} />}
          </Field>
          <div>
            <p className="mb-1.5 text-sm font-medium text-text">Search input</p>
            <SearchInput value={q} onChange={setQ} placeholder="Search websites…" />
          </div>
          <div className="flex flex-wrap items-center gap-4 sm:col-span-2">
            <Segmented label="View mode" value={seg} onChange={setSeg} options={[{ value: "list", label: "List" }, { value: "grid", label: "Grid" }]} />
            <DateRangeSelector value={range} onChange={setRange} />
          </div>
        </Card>
      </Section>

      <Section id="tabs" title="Tabs">
        <Card className="p-5">
          <Tabs defaultValue="overview">
            <TabsList>
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="usage">Usage</TabsTrigger>
              <TabsTrigger value="logs">Logs</TabsTrigger>
            </TabsList>
            <TabsContent value="overview" className="pt-4 text-sm text-muted">Arrow keys move between tabs; the active tab is announced.</TabsContent>
            <TabsContent value="usage" className="pt-4 text-sm text-muted">Usage panel content.</TabsContent>
            <TabsContent value="logs" className="pt-4 text-sm text-muted">Logs panel content.</TabsContent>
          </Tabs>
        </Card>
      </Section>

      <Section id="overlays" title="Dialogs, drawers, menus, tooltips and toasts">
        <Card className="flex flex-wrap items-center gap-2 p-5">
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="outline">Open dialog</Button>
            </DialogTrigger>
            <DialogContent
              title="Add domain"
              description="Dialogs trap focus, close on Escape and return focus to the trigger."
              footer={
                <>
                  <DialogClose asChild>
                    <Button variant="outline">Cancel</Button>
                  </DialogClose>
                  <DialogClose asChild>
                    <Button onClick={() => toast({ title: "Preview only", description: "Domain creation is built in Phase 6." })}>Add</Button>
                  </DialogClose>
                </>
              }
            >
              <Field label="Domain name">{(p) => <Input placeholder="example.com" {...p} />}</Field>
            </DialogContent>
          </Dialog>

          <Button variant="outline" onClick={() => setConfirm(true)}>
            Confirmation dialog
          </Button>
          <ConfirmDialog
            open={confirm}
            onOpenChange={setConfirm}
            destructive
            title="Suspend this tenant?"
            description="Their websites stay online for the grace period and no data is deleted. This is a layout preview only."
            confirmLabel="Suspend"
            onConfirm={() => toast({ title: "Nothing was changed", tone: "info" })}
          />

          <Sheet>
            <SheetTrigger asChild>
              <Button variant="outline">Open drawer</Button>
            </SheetTrigger>
            <SheetContent title="Website details" description="Side drawer for record details and quick edits.">
              <div className="space-y-3 p-5 text-sm text-muted">
                <p>Drawers suit detail views that should keep list context visible.</p>
                <DemoBadge />
              </div>
            </SheetContent>
          </Sheet>

          <Menu>
            <MenuTrigger asChild>
              <Button variant="outline">Action menu</Button>
            </MenuTrigger>
            <MenuContent align="start">
              <MenuItem icon={Plus}>Create</MenuItem>
              <MenuItem icon={Server}>Restart</MenuItem>
              <MenuSeparator />
              <MenuItem icon={Trash2} danger>
                Delete
              </MenuItem>
            </MenuContent>
          </Menu>

          <Tooltip content="Tooltips appear on hover and keyboard focus">
            <IconButton label="Alerts" variant="outline">
              <Bell className="h-4 w-4" />
            </IconButton>
          </Tooltip>

          <Button onClick={() => toast({ title: "Settings saved", description: "Toasts announce politely to screen readers.", tone: "success" })}>
            Show toast
          </Button>
          <Button variant="danger" onClick={() => toast({ title: "Could not reach server", description: "Retry in a moment.", tone: "danger" })}>
            Error toast
          </Button>
        </Card>
      </Section>

      <Section id="states" title="Loading, empty, error and permission states">
        <div className="grid gap-4 lg:grid-cols-2">
          <CardSkeleton />
          <Card>
            <TableSkeleton rows={3} cols={3} />
          </Card>
          <Card>
            <EmptyState title="No websites yet" description="Websites you add will appear here." action={<Button size="sm"><Plus className="h-3.5 w-3.5" /> Add website</Button>} />
          </Card>
          <Card>
            <ErrorState onRetry={() => toast({ title: "Retrying…", tone: "info" })} />
          </Card>
          <Card>
            <PermissionDenied />
          </Card>
          <Card>
            <NotYetAvailable feature="Backups" phase={11} />
          </Card>
        </div>
      </Section>

      <Section id="kpi" title="Metric cards and charts">
        <div className="mb-4 grid gap-4 sm:grid-cols-3">
          <KpiCard label="Websites" value="342" delta={8.4} icon={Server} demo />
          <KpiCard label="Failed jobs" value="3" delta={-25} deltaGoodWhen="down" hint="vs last 7d" demo />
          <KpiCard label="Uptime" value="99.94" unit="%" hint="Not measured yet" />
        </div>
        <TrendChart
          demo
          title="Revenue"
          unit="₹ thousand"
          description="Chart frame: title, unit, legend, tooltip, range control, screen-reader summary and a no-data state."
          stacked
          data={sampleSeries}
          series={[
            { key: "subscriptions", label: "Subscriptions", color: "var(--chart-1)" },
            { key: "addons", label: "Add-ons", color: "var(--chart-2)" },
          ]}
          summary="Sample data: revenue rises gradually."
        />
      </Section>

      <Section id="table" title="Data table pattern">
        <p className="mb-3 text-sm text-muted">
          Search, sort (click headers), filters, column visibility, pagination and row actions. On narrow screens secondary columns collapse and the table scrolls.
        </p>
        <SampleTable />
      </Section>
    </div>
  );
}
