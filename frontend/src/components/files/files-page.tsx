/* eslint-disable react-hooks/set-state-in-effect -- data-fetch effects reset loading/error state by design */
"use client";

import * as React from "react";
import { ChevronRight, FolderOpen, FolderPlus, Home, RefreshCw, RotateCcw, Trash2, Upload } from "lucide-react";
import { ApiError } from "@/lib/http";
import { useApi } from "@/hooks/use-api";
import { useAuth } from "@/components/providers/auth-provider";
import { PageHeader } from "@/components/shell/page-header";
import { AnimatedIcon } from "@/components/ui/animated-icon";
import { Card, CardHeader } from "@/components/ui/card";
import { Button, IconButton } from "@/components/ui/button";
import { ConfirmDialog, Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, NotYetAvailable, PermissionDenied } from "@/components/ui/states";
import { Segmented, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TableSkeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { LiveBadge, fmtBytes, fmtDate } from "@/components/resource/helpers";
import { type Entry, type ListResult, type Quota, type SearchResult, type Site, type StatResult, type TrashItem, AgentTimeout, checkUpload, downloadFile, runOp, uploadFile } from "./api";
import { DetailsSheet } from "./details-sheet";
import { type EntryActions, EntryList } from "./entry-views";
import { QuotaMeter } from "./quota-meter";

const PAGE = 50;
type Prompt = { kind: "mkdir" | "rename" | "move" | "archive"; entry?: Entry } | null;

const msg = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong");

export function FilesPage() {
  const { me, can, loading: authLoading } = useAuth();
  const toast = useToast();
  const sitesApi = useApi<{ items: Site[]; quota: Quota }>(can("files:read") ? "/files/sites" : null);
  const [siteRef, setSiteRef] = React.useState("");
  const site = sitesApi.data?.items.find((s) => s.ref === siteRef) ?? sitesApi.data?.items[0];
  const scope = me?.scope === "platform" && site ? site.tenant_id : "";
  const canWrite = can("files:write");
  const canDelete = can("files:delete");

  const [path, setPath] = React.useState("");
  const [offset, setOffset] = React.useState(0);
  const [view, setView] = React.useState<"list" | "grid">("list");
  const [q, setQ] = React.useState("");
  const [term, setTerm] = React.useState("");
  const [tick, setTick] = React.useState(0);
  const [data, setData] = React.useState<ListResult | SearchResult | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState<string>();
  const [timedOut, setTimedOut] = React.useState(false);
  const [waiting, setWaiting] = React.useState<string>();
  const [busy, setBusy] = React.useState<string>();
  const [prompt, setPrompt] = React.useState<Prompt>(null);
  const [del, setDel] = React.useState<Entry | null>(null);
  const [purge, setPurge] = React.useState<TrashItem | "all" | null>(null);
  const [details, setDetails] = React.useState<Entry | null>(null);
  const [stat, setStat] = React.useState<StatResult>();
  const [statErr, setStatErr] = React.useState<string>();
  const [statLoading, setStatLoading] = React.useState(false);
  const [upPct, setUpPct] = React.useState<number | null>(null);
  const [upName, setUpName] = React.useState("");
  const [trash, setTrash] = React.useState<TrashItem[] | null>(null);
  const [trashErr, setTrashErr] = React.useState<string>();
  const [tab, setTab] = React.useState("files");
  const fileInput = React.useRef<HTMLInputElement>(null);
  const reload = () => setTick((t) => t + 1);
  const siteKey = site?.ref ?? "";
  const quotaApi = useApi<Quota>(site ? "/files/quota" : null, scope ? { tenant_id: scope } : undefined);
  const history = useApi<{ items: { id: string; op: string; path: string | null; created_at: string }[] }>(site ? "/files/history" : null, { site: siteKey, limit: 8, ...(scope ? { tenant_id: scope } : {}) });

  // Directory listing / search
  React.useEffect(() => {
    if (!siteKey || tab !== "files") return;
    let alive = true;
    setLoading(true);
    setErr(undefined);
    setTimedOut(false);
    const body = term ? { path, query: term, limit: PAGE, offset } : { path, limit: PAGE, offset, show_hidden: false };
    runOp<ListResult & SearchResult>(siteKey, scope, term ? "search" : "list", body, (s) => alive && setWaiting(s))
      .then((j) => alive && setData(j.result as ListResult | SearchResult))
      .catch((e) => {
        if (!alive) return;
        setData(null);
        if (e instanceof AgentTimeout) setTimedOut(true);
        setErr(msg(e));
      })
      .finally(() => alive && (setLoading(false), setWaiting(undefined)));
    return () => {
      alive = false;
    };
  }, [siteKey, scope, path, offset, term, tick, tab]);

  // Trash
  React.useEffect(() => {
    if (!siteKey || tab !== "trash") return;
    let alive = true;
    setTrashErr(undefined);
    runOp<{ items: TrashItem[] }>(siteKey, scope, "trash/list", {})
      .then((j) => alive && setTrash((j.result as { items: TrashItem[] }).items))
      .catch((e) => alive && setTrashErr(msg(e)));
    return () => {
      alive = false;
    };
  }, [siteKey, scope, tab, tick]);

  async function act<T>(label: string, fn: () => Promise<T>, ok?: string) {
    setBusy(label);
    try {
      const r = await fn();
      if (ok) toast({ title: ok, tone: "success" });
      reload();
      quotaApi.reload();
      history.reload();
      return r;
    } catch (e) {
      toast({ title: `${label} failed`, description: msg(e), tone: "danger" });
    } finally {
      setBusy(undefined);
    }
  }

  const join = (dir: string, name: string) => (dir ? `${dir}/${name}` : name);
  const parentOf = (p: string) => p.split("/").slice(0, -1).join("/");
  const crumbs = path ? path.split("/") : [];
  const go = (p: string) => {
    setPath(p);
    setOffset(0);
    setTerm("");
    setQ("");
  };

  const openDetails = (e: Entry) => {
    setDetails(e);
    setStat(undefined);
    setStatErr(undefined);
    setStatLoading(true);
    runOp<StatResult>(siteKey, scope, "stat", { path: e.path })
      .then((j) => setStat(j.result as StatResult))
      .catch((x) => setStatErr(msg(x)))
      .finally(() => setStatLoading(false));
  };

  const actions: EntryActions = {
    open: (e) => (e.type === "dir" ? go(e.path) : openDetails(e)),
    details: openDetails,
    download: (e) => void act("Download", () => downloadFile(siteKey, scope, e.path, e.name)),
    rename: (e) => setPrompt({ kind: "rename", entry: e }),
    move: (e) => setPrompt({ kind: "move", entry: e }),
    archive: (e) => setPrompt({ kind: "archive", entry: e }),
    extract: (e) => void act("Extract", () => runOp(siteKey, scope, "extract", { archive: e.path, dest_dir: parentOf(e.path) }), `Extracted ${e.name}`),
    remove: setDel,
  };

  async function onFiles(files: FileList | null) {
    if (!files?.length) return;
    for (const f of Array.from(files)) {
      const bad = checkUpload(f);
      if (bad) {
        toast({ title: `Cannot upload ${f.name}`, description: bad, tone: "danger" });
        continue;
      }
      setUpName(f.name);
      setUpPct(0);
      try {
        await uploadFile(siteKey, scope, path, f, false, setUpPct, (s) => setWaiting(s));
        toast({ title: `Uploaded ${f.name}`, tone: "success" });
      } catch (e) {
        const x = e instanceof ApiError && e.code === "quota_exceeded" ? "Your storage quota is full." : msg(e);
        toast({ title: `Upload of ${f.name} failed`, description: x, tone: "danger" });
        break;
      }
    }
    setUpPct(null);
    setWaiting(undefined);
    if (fileInput.current) fileInput.current.value = "";
    reload();
    quotaApi.reload();
    history.reload();
  }

  async function submitPrompt(name: string, extra: string) {
    const p = prompt!;
    if (p.kind === "mkdir") await runOp(siteKey, scope, "mkdir", { path: join(path, name) });
    else if (p.kind === "rename") await runOp(siteKey, scope, "rename", { src: p.entry!.path, dst: join(parentOf(p.entry!.path), name) });
    else if (p.kind === "move") await runOp(siteKey, scope, "rename", { src: p.entry!.path, dst: join(name.replace(/^\/+|\/+$/g, ""), p.entry!.name) });
    else await runOp(siteKey, scope, "archive", { paths: [p.entry!.path], dest: join(parentOf(p.entry!.path), name), format: extra });
    reload();
    history.reload();
  }

  if (authLoading) return null;
  if (!can("files:read")) return <><Header /><Card><PermissionDenied /></Card></>;
  if (sitesApi.loading) return <><Header /><Card><TableSkeleton rows={5} cols={3} /></Card></>;
  if (sitesApi.error) return <><Header /><Card><ErrorState onRetry={sitesApi.reload} /></Card></>;
  const sites = sitesApi.data?.items ?? [];
  if (!site)
    return (
      <>
        <Header />
        <Card><EmptyState title="No websites yet" description="Websites you own will appear here once they are set up." /></Card>
      </>
    );
  if (!site.configured)
    return (
      <>
        <Header sites={sites} site={siteKey} onSite={setSiteRef} />
        <Card><NotYetAvailable feature="File manager for this website" phase={10} description="This website has no file location registered on a server yet. Ask support to finish provisioning." /></Card>
      </>
    );

  const entries = data?.entries ?? [];
  const total = data?.total ?? 0;
  const searching = !!term;
  const quota = quotaApi.data ?? sitesApi.data?.quota;

  return (
    <>
      <Header sites={sites} site={siteKey} onSite={(s) => { setSiteRef(s); go(""); setData(null); }} />
      <div className="space-y-4">
        <QuotaMeter quota={quota} />
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="files" className="min-h-11">Files</TabsTrigger>
            <TabsTrigger value="trash" className="min-h-11">Trash</TabsTrigger>
            <TabsTrigger value="activity" className="min-h-11">Activity</TabsTrigger>
          </TabsList>

          <TabsContent value="files" className="mt-4">
            <Card>
              <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
                <nav aria-label="Breadcrumb" className="flex min-w-0 flex-1 basis-full flex-wrap items-center gap-0.5 text-sm md:basis-auto">
                  <button type="button" onClick={() => go("")} className="inline-flex min-h-11 items-center gap-1 rounded-md px-2 text-muted hover:bg-subtle hover:text-text">
                    <Home className="h-4 w-4" /> <span>{site.label}</span>
                  </button>
                  {crumbs.map((c, i) => (
                    <React.Fragment key={i}>
                      <ChevronRight className="h-3.5 w-3.5 text-faint" aria-hidden />
                      <button type="button" aria-current={i === crumbs.length - 1 ? "page" : undefined} onClick={() => go(crumbs.slice(0, i + 1).join("/"))} className="min-h-11 rounded-md px-2 text-muted hover:bg-subtle hover:text-text aria-[current=page]:font-semibold aria-[current=page]:text-text">
                        {c}
                      </button>
                    </React.Fragment>
                  ))}
                </nav>
                <form className="flex min-w-0 flex-1 gap-2" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); setOffset(0); }}>
                  <Input aria-label="Search files" placeholder="Search names in this folder…" value={q} onChange={(e) => setQ(e.target.value)} className="h-11 md:h-9" />
                  <Button type="submit" variant="outline" className="min-h-11 md:min-h-0">Search</Button>
                  {searching && <Button type="button" variant="ghost" onClick={() => { setQ(""); setTerm(""); setOffset(0); }}>Clear</Button>}
                </form>
                <LiveBadge />
                <Segmented label="View" value={view} onChange={setView} options={[{ value: "list", label: "List" }, { value: "grid", label: "Grid" }]} />
                <IconButton label="Refresh" variant="outline" className="h-11 w-11 md:h-9 md:w-9" onClick={reload}><RefreshCw className="h-4 w-4" /></IconButton>
                {canWrite && (
                  <>
                    <Button variant="outline" className="min-h-11 md:min-h-0" onClick={() => setPrompt({ kind: "mkdir" })}><FolderPlus className="h-4 w-4" /> New folder</Button>
                    <Button className="min-h-11 md:min-h-0" loading={upPct !== null} onClick={() => fileInput.current?.click()}><Upload className="h-4 w-4" /> Upload</Button>
                    <input ref={fileInput} type="file" multiple hidden onChange={(e) => void onFiles(e.target.files)} aria-label="Choose files to upload" />
                  </>
                )}
              </div>
              {upPct !== null && (
                <div className="border-b border-border px-4 py-2 text-sm" role="status">
                  <div className="mb-1 flex justify-between"><span className="truncate">Uploading {upName}</span><span className="tnum">{upPct}%</span></div>
                  <div role="progressbar" aria-valuenow={upPct} aria-valuemin={0} aria-valuemax={100} className="h-1.5 overflow-hidden rounded-full bg-subtle"><div className="h-full bg-primary transition-all" style={{ width: `${upPct}%` }} /></div>
                </div>
              )}
              {busy && <p className="border-b border-border px-4 py-2 text-sm text-muted" role="status">{busy}…</p>}
              {loading && !data ? (
                <div>
                  {waiting && <p className="px-4 pt-3 text-sm text-muted" role="status">Waiting for the server agent ({waiting})…</p>}
                  <TableSkeleton rows={6} cols={3} />
                </div>
              ) : err ? (
                <ErrorState title={timedOut ? "The server agent has not responded" : "Could not load files"} description={err} onRetry={reload} />
              ) : entries.length === 0 ? (
                <EmptyState title={searching ? "No matches" : "This folder is empty"} description={searching ? `Nothing named “${term}” was found here.` : canWrite ? "Upload files or create a folder to get started." : undefined} />
              ) : (
                <>
                  <EntryList entries={entries} view={view} a={actions} canWrite={canWrite} canDelete={canDelete} />
                  {(searching && (data as SearchResult)?.scan_truncated) && <p className="px-4 pb-2 text-xs text-warning">Search stopped early on a very large folder; refine your term.</p>}
                  <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-3 text-sm text-muted">
                    <span className="tnum">{offset + 1}–{Math.min(total, offset + PAGE)} of {total}</span>
                    <div className="flex gap-2">
                      <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
                      <Button variant="outline" size="sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
                    </div>
                  </div>
                </>
              )}
            </Card>
          </TabsContent>

          <TabsContent value="trash" className="mt-4">
            <Card>
              <CardHeader title="Trash" description="Deleted items are recoverable until purged." actions={canDelete && trash && trash.length > 0 ? <Button variant="danger" size="sm" onClick={() => setPurge("all")}>Empty trash</Button> : undefined} />
              {trashErr ? <ErrorState description={trashErr} onRetry={reload} /> : trash === null ? <TableSkeleton rows={3} cols={3} /> : trash.length === 0 ? <EmptyState title="Trash is empty" /> : (
                <ul className="divide-y divide-border">
                  {trash.map((t) => (
                    <li key={t.id} className="flex flex-wrap items-center gap-3 px-4 py-2">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-text">{t.name}</p>
                        <p className="truncate text-xs text-muted">/{t.path} · {fmtBytes(t.size)} · deleted {fmtDate(t.deleted_at)}</p>
                      </div>
                      {canWrite && <Button variant="outline" size="sm" className="min-h-11 md:min-h-0" onClick={() => void act("Restore", () => runOp(siteKey, scope, "restore", { trash_id: t.id }), `Restored ${t.name}`)}><RotateCcw className="h-3.5 w-3.5" /> Restore</Button>}
                      {canDelete && <Button variant="danger" size="sm" className="min-h-11 md:min-h-0" onClick={() => setPurge(t)}><Trash2 className="h-3.5 w-3.5" /> Delete forever</Button>}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </TabsContent>

          <TabsContent value="activity" className="mt-4">
            <Card>
              <CardHeader title="Recent activity" description="File actions recorded for this website." />
              {history.loading ? <TableSkeleton rows={3} cols={3} /> : history.error ? <ErrorState onRetry={history.reload} /> : !history.data?.items.length ? <EmptyState title="No activity yet" /> : (
                <ul className="divide-y divide-border">
                  {history.data.items.map((h) => (
                    <li key={h.id} className="flex flex-wrap justify-between gap-2 px-4 py-2 text-sm">
                      <span className="truncate"><strong className="font-medium capitalize">{h.op.replace("_", " ")}</strong> <span className="text-muted">{h.path ? `/${h.path}` : ""}</span></span>
                      <span className="tnum text-muted">{fmtDate(h.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </TabsContent>
        </Tabs>
      </div>

      <DetailsSheet entry={details} stat={stat} loading={statLoading} error={statErr} onClose={() => setDetails(null)} a={actions} canWrite={canWrite} canDelete={canDelete} />
      <PromptDialog prompt={prompt} onClose={() => setPrompt(null)} onSubmit={submitPrompt} path={path} />
      <ConfirmDialog open={!!del} onOpenChange={(o) => !o && setDel(null)} destructive title="Move to trash?" description={`“${del?.name}” will be moved to the trash. You can restore it until the trash is emptied.`} confirmLabel="Move to trash" onConfirm={() => del && void act("Delete", () => runOp(siteKey, scope, "delete", { path: del.path, confirm: true }), `Moved ${del.name} to trash`)} />
      <ConfirmDialog open={!!purge} onOpenChange={(o) => !o && setPurge(null)} destructive title="Permanently delete?" description={purge === "all" ? "Everything in the trash will be permanently deleted. This cannot be undone." : `“${purge?.name ?? ""}” will be permanently deleted. This cannot be undone.`} confirmLabel="Delete forever" onConfirm={() => { const p = purge; if (p) void act("Purge", () => runOp(siteKey, scope, "purge", p === "all" ? { empty: true, confirm: true } : { trash_id: p.id, confirm: true }), "Permanently deleted"); }} />
    </>
  );
}

function Header({ sites, site, onSite }: { sites?: Site[]; site?: string; onSite?: (s: string) => void }) {
  return (
    <PageHeader
      title="Files"
      description="Browse and manage your website files. Deleted items go to the trash first."
      actions={
        <>
          <AnimatedIcon icon={FolderOpen} idle="float" className="text-primary" />
          {sites && sites.length > 1 && onSite && (
            <Select aria-label="Website" value={site} onChange={(e) => onSite(e.target.value)} className="h-11 w-auto md:h-9">
              {sites.map((s) => <option key={s.ref} value={s.ref}>{s.label}</option>)}
            </Select>
          )}
        </>
      }
    />
  );
}

function PromptDialog({ prompt, onClose, onSubmit, path }: { prompt: Prompt; onClose: () => void; onSubmit: (name: string, extra: string) => Promise<void>; path: string }) {
  const [val, setVal] = React.useState("");
  const [fmt, setFmt] = React.useState("zip");
  const [err, setErr] = React.useState<string>();
  const [pending, setPending] = React.useState(false);
  React.useEffect(() => {
    if (!prompt) return;
    setErr(undefined);
    setFmt("zip");
    setVal(prompt.kind === "rename" ? prompt.entry!.name : prompt.kind === "move" ? path : prompt.kind === "archive" ? `${prompt.entry!.name}.zip` : "");
  }, [prompt, path]);
  const title = { mkdir: "New folder", rename: "Rename", move: "Move to folder", archive: "Create archive" }[prompt?.kind ?? "mkdir"];
  const label = { mkdir: "Folder name", rename: "New name", move: "Destination folder (path from site root, empty for root)", archive: "Archive name" }[prompt?.kind ?? "mkdir"];
  return (
    <Dialog open={!!prompt} onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={title}>
        <form
          className="space-y-4"
          onSubmit={async (e) => {
            e.preventDefault();
            setPending(true);
            setErr(undefined);
            try {
              await onSubmit(val.trim(), fmt);
              onClose();
            } catch (x) {
              setErr(msg(x));
            } finally {
              setPending(false);
            }
          }}
        >
          {prompt?.kind === "archive" && (
            <Field label="Format">{(p) => <Select {...p} value={fmt} onChange={(e) => { setFmt(e.target.value); setVal((v) => v.replace(/\.(zip|tar\.gz)$/, "") + (e.target.value === "zip" ? ".zip" : ".tar.gz")); }}><option value="zip">zip</option><option value="tar.gz">tar.gz</option></Select>}</Field>
          )}
          <Field label={label} error={err}>{(p) => <Input {...p} autoFocus value={val} onChange={(e) => setVal(e.target.value)} className="h-11" />}</Field>
          <div className="flex justify-end gap-2">
            <DialogClose asChild><Button type="button" variant="outline">Cancel</Button></DialogClose>
            <Button type="submit" loading={pending} disabled={prompt?.kind !== "move" && !val.trim()}>OK</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
