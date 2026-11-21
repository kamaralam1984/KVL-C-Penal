"use client";

import * as React from "react";
import { Ban, Code2, Image as ImageIcon, Lock, Plus, Shuffle, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { useApi } from "@/hooks/use-api";
import { useAuth } from "@/components/providers/auth-provider";
import { ApiError, patch, put } from "@/lib/http";

interface Php { memory_limit_mb: number; upload_max_mb: number; post_max_mb: number; max_execution_time: number; max_input_vars: number; display_errors: boolean }
interface Redirect { source: string; target: string; code: 301 | 302 | 307 | 308; match: "exact" | "prefix" }
interface PUser { username: string; password?: string }
interface Protected { path: string; users: PUser[] }
interface Hotlink { enabled: boolean; allow_empty_referer: boolean; allowed_hosts: string[] }
interface Options { php: Php | null; redirects: Redirect[]; protected: Protected[]; deny_ips: string[]; hotlink: Hotlink }
interface View { options: Options; runtime: string; php_available: boolean; provision_state: string }

const PHP_DEFAULT: Php = { memory_limit_mb: 128, upload_max_mb: 64, post_max_mb: 64, max_execution_time: 60, max_input_vars: 1000, display_errors: false };
const msg = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong");

function Row({ children }: { children: React.ReactNode }) {
  return <div className="grid gap-2 rounded-lg border border-border p-3 sm:grid-cols-[1fr_1fr_auto_auto_auto] sm:items-end">{children}</div>;
}

/** cPanel-style site tools. Saving validates on the server and re-runs the safe nginx pipeline (stage → nginx -t). */
export function SiteTools({ websiteId, runtime, runtimeVersion, onSaved }: { websiteId: string; runtime: string; runtimeVersion: string | null; onSaved: () => void }) {
  const toast = useToast();
  const { can } = useAuth();
  const write = can("websites:write");
  const view = useApi<View>(`/websites/${websiteId}/options`);
  const runtimes = useApi<{ installed: Record<string, string[]>; supported: Record<string, string[]> }>(runtime === "php" ? "/apps/runtimes" : null);
  const [o, setO] = React.useState<Options | null>(null);
  const [ipText, setIpText] = React.useState("");
  const [hostText, setHostText] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [ver, setVer] = React.useState(runtimeVersion ?? "");

  React.useEffect(() => {
    if (view.data) {
      /* eslint-disable react-hooks/set-state-in-effect -- hydrate the editable copy once loaded */
      setO(view.data.options);
      setIpText(view.data.options.deny_ips.join("\n"));
      setHostText(view.data.options.hotlink.allowed_hosts.join("\n"));
      /* eslint-enable react-hooks/set-state-in-effect */
    }
  }, [view.data]);

  if (!o) return <p className="text-sm text-muted">{view.error ? view.error.message : "Loading site tools…"}</p>;
  const set = (p: Partial<Options>) => setO({ ...o, ...p });
  const lines = (t: string) => t.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
  const versions = runtimes.data ? (runtimes.data.installed.php?.length ? runtimes.data.installed.php : runtimes.data.supported.php ?? []) : [];

  async function save() {
    setSaving(true);
    try {
      const body = { ...o!, deny_ips: lines(ipText), hotlink: { ...o!.hotlink, allowed_hosts: lines(hostText) }, php: runtime === "php" ? o!.php : null };
      const r = await put<View & { restaged: boolean }>(`/websites/${websiteId}/options`, body);
      setO(r.options);
      toast({ title: "Site settings saved", description: r.restaged ? "The new Nginx config is being validated. An operator activates and reloads it." : "Saved. They apply when the website is provisioned.", tone: "success" });
      onSaved();
    } catch (e) {
      toast({ title: "Not saved", description: msg(e), tone: "danger" });
    } finally {
      setSaving(false);
    }
  }

  async function changeVersion() {
    try {
      await patch(`/websites/${websiteId}`, { runtime_version: ver });
      toast({ title: `PHP ${ver} selected`, description: "Click Validate config to apply it.", tone: "success" });
      onSaved();
    } catch (e) {
      toast({ title: "Version not changed", description: msg(e), tone: "danger" });
    }
  }

  const num = (k: keyof Php, label: string, min: number, max: number, unit = "") => (
    <Field label={`${label}${unit ? ` (${unit})` : ""}`} hint={`${min}–${max}`}>{(p) => <Input {...p} type="number" min={min} max={max} disabled={!write} value={o.php?.[k] as number} onChange={(e) => set({ php: { ...(o.php ?? PHP_DEFAULT), [k]: Number(e.target.value) } })} />}</Field>
  );

  return (
    <div className="space-y-3">
      <Tabs defaultValue={runtime === "php" ? "php" : "redirects"}>
        <TabsList className="mb-3 overflow-x-auto">
          {runtime === "php" && <TabsTrigger value="php"><Code2 className="h-3.5 w-3.5" />PHP</TabsTrigger>}
          <TabsTrigger value="redirects"><Shuffle className="h-3.5 w-3.5" />Redirects</TabsTrigger>
          <TabsTrigger value="protected"><Lock className="h-3.5 w-3.5" />Protect folders</TabsTrigger>
          <TabsTrigger value="ips"><Ban className="h-3.5 w-3.5" />IP blocker</TabsTrigger>
          <TabsTrigger value="hotlink"><ImageIcon className="h-3.5 w-3.5" />Hotlink</TabsTrigger>
        </TabsList>

        {runtime === "php" && (
          <TabsContent value="php" className="space-y-4">
            <div className="flex flex-wrap items-end gap-2">
              <Field label="PHP version" hint="Only versions installed on the server can be chosen.">{(p) => <Select {...p} disabled={!write} value={ver} onChange={(e) => setVer(e.target.value)}>{versions.map((v) => <option key={v}>{v}</option>)}</Select>}</Field>
              {write && <Button size="sm" variant="outline" disabled={!ver || ver === runtimeVersion} onClick={() => void changeVersion()}>Change version</Button>}
            </div>
            <label className="flex items-center gap-2 text-sm text-text"><input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" disabled={!write} checked={!!o.php} onChange={(e) => set({ php: e.target.checked ? PHP_DEFAULT : null })} />Custom php.ini values for this site</label>
            {o.php && (
              <div className="grid gap-3 sm:grid-cols-3">
                {num("memory_limit_mb", "memory_limit", 32, 1024, "MB")}
                {num("upload_max_mb", "upload_max_filesize", 1, 512, "MB")}
                {num("post_max_mb", "post_max_size", 1, 512, "MB")}
                {num("max_execution_time", "max_execution_time", 5, 300, "s")}
                {num("max_input_vars", "max_input_vars", 1000, 20000)}
                <label className="flex items-center gap-2 self-end pb-2 text-sm text-text"><input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" disabled={!write} checked={o.php.display_errors} onChange={(e) => set({ php: { ...o.php!, display_errors: e.target.checked } })} />display_errors (debug only)</label>
              </div>
            )}
          </TabsContent>
        )}

        <TabsContent value="redirects" className="space-y-2">
          <p className="text-xs text-muted">Send visitors from an old path to a new path or URL. Use a path like /old-page, or /blog/ with “starts with”.</p>
          {o.redirects.map((r, i) => (
            <Row key={i}>
              <Field label="From">{(p) => <Input {...p} disabled={!write} value={r.source} placeholder="/old-page" onChange={(e) => set({ redirects: o.redirects.map((x, j) => (j === i ? { ...x, source: e.target.value } : x)) })} />}</Field>
              <Field label="To">{(p) => <Input {...p} disabled={!write} value={r.target} placeholder="/new-page or https://…" onChange={(e) => set({ redirects: o.redirects.map((x, j) => (j === i ? { ...x, target: e.target.value } : x)) })} />}</Field>
              <Field label="Type">{(p) => <Select {...p} disabled={!write} value={String(r.code)} onChange={(e) => set({ redirects: o.redirects.map((x, j) => (j === i ? { ...x, code: Number(e.target.value) as Redirect["code"] } : x)) })}><option value="301">301 permanent</option><option value="302">302 temporary</option><option value="307">307</option><option value="308">308</option></Select>}</Field>
              <Field label="Match">{(p) => <Select {...p} disabled={!write} value={r.match} onChange={(e) => set({ redirects: o.redirects.map((x, j) => (j === i ? { ...x, match: e.target.value as Redirect["match"] } : x)) })}><option value="exact">Exact</option><option value="prefix">Starts with</option></Select>}</Field>
              {write && <Button size="sm" variant="ghost" onClick={() => set({ redirects: o.redirects.filter((_, j) => j !== i) })}><Trash2 className="h-4 w-4" /><span className="sr-only">Remove redirect</span></Button>}
            </Row>
          ))}
          {write && o.redirects.length < 50 && <Button size="sm" variant="outline" onClick={() => set({ redirects: [...o.redirects, { source: "", target: "", code: 301, match: "exact" }] })}><Plus className="h-4 w-4" />Add redirect</Button>}
        </TabsContent>

        <TabsContent value="protected" className="space-y-3">
          <p className="text-xs text-muted">Ask for a username and password before showing a folder (for example /admin/). Passwords are stored only as salted hashes; leave a password blank to keep it.</p>
          {o.protected.map((pp, i) => (
            <div key={i} className="space-y-2 rounded-lg border border-border p-3">
              <div className="flex items-end gap-2">
                <div className="flex-1"><Field label="Folder">{(p) => <Input {...p} disabled={!write} value={pp.path} placeholder="/admin/" onChange={(e) => set({ protected: o.protected.map((x, j) => (j === i ? { ...x, path: e.target.value } : x)) })} />}</Field></div>
                {write && <Button size="sm" variant="ghost" onClick={() => set({ protected: o.protected.filter((_, j) => j !== i) })}><Trash2 className="h-4 w-4" /><span className="sr-only">Remove folder</span></Button>}
              </div>
              {pp.users.map((u, k) => (
                <div key={k} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
                  <Field label="Username">{(p) => <Input {...p} disabled={!write} value={u.username} autoComplete="off" onChange={(e) => set({ protected: o.protected.map((x, j) => (j === i ? { ...x, users: x.users.map((y, m) => (m === k ? { ...y, username: e.target.value } : y)) } : x)) })} />}</Field>
                  <Field label="Password" hint="8+ characters">{(p) => <Input {...p} type="password" disabled={!write} autoComplete="new-password" value={u.password ?? ""} placeholder="•••••••• (unchanged)" onChange={(e) => set({ protected: o.protected.map((x, j) => (j === i ? { ...x, users: x.users.map((y, m) => (m === k ? { ...y, password: e.target.value || undefined } : y)) } : x)) })} />}</Field>
                  {write && pp.users.length > 1 && <Button size="sm" variant="ghost" onClick={() => set({ protected: o.protected.map((x, j) => (j === i ? { ...x, users: x.users.filter((_, m) => m !== k) } : x)) })}><Trash2 className="h-4 w-4" /><span className="sr-only">Remove user</span></Button>}
                </div>
              ))}
              {write && pp.users.length < 20 && <Button size="sm" variant="ghost" onClick={() => set({ protected: o.protected.map((x, j) => (j === i ? { ...x, users: [...x.users, { username: "" }] } : x)) })}><Plus className="h-4 w-4" />Add user</Button>}
            </div>
          ))}
          {write && o.protected.length < 10 && <Button size="sm" variant="outline" onClick={() => set({ protected: [...o.protected, { path: "", users: [{ username: "" }] }] })}><Plus className="h-4 w-4" />Protect a folder</Button>}
        </TabsContent>

        <TabsContent value="ips" className="space-y-2">
          <Field label="Blocked IP addresses or ranges" hint="One per line, e.g. 203.0.113.7 or 198.51.100.0/24. Blocked visitors get 403 Forbidden. Works behind the Cloudflare tunnel.">{(p) => <textarea {...p} rows={8} disabled={!write} className="w-full rounded-md border border-border bg-surface p-2 font-mono text-sm text-text" value={ipText} onChange={(e) => setIpText(e.target.value)} />}</Field>
        </TabsContent>

        <TabsContent value="hotlink" className="space-y-3">
          <label className="flex items-center gap-2 text-sm text-text"><input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" disabled={!write} checked={o.hotlink.enabled} onChange={(e) => set({ hotlink: { ...o.hotlink, enabled: e.target.checked } })} />Stop other websites from embedding my images, video, audio and PDFs</label>
          <label className="flex items-center gap-2 text-sm text-text"><input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" disabled={!write || !o.hotlink.enabled} checked={o.hotlink.allow_empty_referer} onChange={(e) => set({ hotlink: { ...o.hotlink, allow_empty_referer: e.target.checked } })} />Allow direct visits (no referrer)</label>
          <Field label="Also allow these sites" hint="One per line, e.g. *.google.com">{(p) => <textarea {...p} rows={4} disabled={!write || !o.hotlink.enabled} className="w-full rounded-md border border-border bg-surface p-2 font-mono text-sm text-text" value={hostText} onChange={(e) => setHostText(e.target.value)} />}</Field>
        </TabsContent>
      </Tabs>
      {write && <Button loading={saving} onClick={() => void save()}>Save site settings</Button>}
    </div>
  );
}
