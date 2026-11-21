"use client";

import * as React from "react";
import { ArrowUpCircle, Construction, RefreshCw, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { StatusPill, Can } from "@/components/resource/helpers";
import { useToast } from "@/components/ui/toast";
import { ApiError, get, post } from "@/lib/http";

interface Item { slug: string; name: string; version: string | null; latest: string | null; update: boolean; managed: boolean }
interface Status { status: string; core: { version: string | null; latest: string | null; update: boolean }; plugins: Item[]; themes: Item[]; maintenance: boolean }
type Kind = "plugin" | "theme";

const msg = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong");
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** WordPress toolkit. Updates come only from wordpress.org; the previous version is kept for one-click rollback. */
export function WordPressToolkit({ appId }: { appId: string }) {
  const toast = useToast();
  const base = `/apps/${appId}/wordpress`;
  const [data, setData] = React.useState<Status | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [err, setErr] = React.useState<string>();
  const [confirm, setConfirm] = React.useState<null | { kind: Kind; item: Item; action: "update" | "rollback" }>(null);

  const wait = React.useCallback(async <T,>(jobId: string): Promise<T> => {
    for (let i = 0; i < 120; i++) {
      const r = await get<T & { status: string; message?: string }>(`${base}/jobs/${jobId}`);
      if (r.status === "succeeded") return r;
      if (["failed", "timed_out", "cancelled"].includes(r.status)) throw new Error(r.message ?? `Request ${r.status}`);
      await sleep(i < 5 ? 500 : 1500);
    }
    throw new Error("The server agent has not answered yet.");
  }, [base]);

  const load = React.useCallback(async () => {
    setBusy("status");
    setErr(undefined);
    try {
      const { job_id } = await post<{ job_id: string }>(`${base}/status`);
      setData(await wait<Status>(job_id));
    } catch (e) {
      setErr(msg(e));
    } finally {
      setBusy(null);
    }
  }, [base, wait]);

  async function act(label: string, path: string, body: unknown, ok: string) {
    setBusy(label);
    try {
      const { job_id } = await post<{ job_id: string }>(`${base}/${path}`, body);
      await wait(job_id);
      toast({ title: ok, tone: "success" });
      await load();
    } catch (e) {
      toast({ title: `${label} failed`, description: msg(e), tone: "danger" });
    } finally {
      setBusy(null);
    }
  }

  if (!data) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted">See WordPress, plugin and theme versions, update from wordpress.org and switch maintenance mode. Your site&apos;s PHP code is never run by the panel to do this.</p>
        {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err}</p>}
        <Button loading={busy === "status"} onClick={() => void load()}><RefreshCw className="h-4 w-4" />Check WordPress</Button>
      </div>
    );
  }

  const list = (kind: Kind, items: Item[]) => (
    <section className="space-y-2">
      <h4 className="text-sm font-semibold text-text capitalize">{kind}s ({items.length})</h4>
      <ul className="divide-y divide-border rounded-md border border-border">
        {items.map((it) => (
          <li key={it.slug} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
            <div className="min-w-0"><p className="truncate font-medium text-text">{it.name}</p><p className="text-xs text-muted">{it.slug} · {it.version ?? "?"}{it.update ? ` → ${it.latest}` : it.latest ? " · up to date" : it.managed ? "" : " · not from wordpress.org"}</p></div>
            <Can perm="apps:write">
              <div className="flex gap-1">
                {it.update && <Button size="sm" loading={busy === `u-${it.slug}`} onClick={() => setConfirm({ kind, item: it, action: "update" })}><ArrowUpCircle className="h-3.5 w-3.5" />Update</Button>}
                {it.managed && <Button size="sm" variant="ghost" onClick={() => setConfirm({ kind, item: it, action: "rollback" })}><RotateCcw className="h-3.5 w-3.5" /><span className="sr-only">Roll back {it.name}</span></Button>}
              </div>
            </Can>
          </li>
        ))}
        {!items.length && <li className="px-3 py-3 text-sm text-muted">None installed.</li>}
      </ul>
    </section>
  );

  const updates = data.plugins.filter((p) => p.update).length + data.themes.filter((t) => t.update).length;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-border p-3"><p className="text-xs text-muted">WordPress</p><p className="text-lg font-semibold text-text">{data.core.version ?? "?"}</p>{data.core.update ? <p className="text-xs text-warning">{data.core.latest} available: update from WP admin → Updates</p> : <p className="text-xs text-muted">{data.core.latest ? "Up to date" : "Latest version unknown"}</p>}</div>
        <div className="rounded-lg border border-border p-3"><p className="text-xs text-muted">Updates available</p><p className="text-lg font-semibold text-text tabular-nums">{updates}</p></div>
        <div className="rounded-lg border border-border p-3">
          <p className="text-xs text-muted">Maintenance mode</p>
          <div className="mt-1 flex items-center gap-2"><StatusPill status={data.maintenance ? "maintenance" : "active"} />
            <Can perm="apps:write"><Button size="sm" variant="outline" loading={busy === "Maintenance"} onClick={() => void act("Maintenance", "maintenance", { enabled: !data.maintenance }, data.maintenance ? "Site is live again" : "Maintenance mode on")}><Construction className="h-3.5 w-3.5" />{data.maintenance ? "Turn off" : "Turn on"}</Button></Can>
          </div>
        </div>
      </div>
      {list("plugin", data.plugins)}
      {list("theme", data.themes)}
      <Button size="sm" variant="outline" loading={busy === "status"} onClick={() => void load()}><RefreshCw className="h-4 w-4" />Refresh</Button>
      <ConfirmDialog
        open={!!confirm}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={confirm?.action === "update" ? `Update ${confirm?.item.name}?` : `Roll back ${confirm?.item.name}?`}
        description={confirm?.action === "update" ? `Installs version ${confirm?.item.latest} from wordpress.org. The current version (${confirm?.item.version}) is kept so you can roll back.` : "Restores the version that was installed before the last update."}
        confirmLabel={confirm?.action === "update" ? "Update" : "Roll back"}
        onConfirm={() => { const c = confirm; if (!c) return; void act(c.action === "update" ? `u-${c.item.slug}` : "Rollback", c.action, { kind: c.kind, slug: c.item.slug }, c.action === "update" ? `${c.item.name} updated` : `${c.item.name} rolled back`); }}
      />
    </div>
  );
}
