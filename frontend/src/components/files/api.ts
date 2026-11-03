import { ApiError, api, get } from "@/lib/http";

export interface Entry {
  name: string;
  path: string;
  type: "file" | "dir" | "symlink" | "other";
  size: number;
  mtime: string;
  mode: string;
  octal: string;
  hidden: boolean;
  ext: string;
}
export interface Job<R = Record<string, unknown>> {
  id: string;
  operation: string;
  status: string;
  result: (R & { ok?: boolean; downloadable?: boolean }) | null;
  error: string | null;
}
export interface ListResult {
  path: string;
  total: number;
  offset: number;
  entries: Entry[];
}
export interface SearchResult {
  total: number;
  entries: Entry[];
  scan_truncated: boolean;
}
export interface StatResult {
  entry: Entry;
  permissions: { owner_uid: number; owner_gid: number; readable: boolean; writable: boolean; mode: string; octal: string };
}
export interface TrashItem {
  id: string;
  path: string;
  name: string;
  size: number;
  deleted_at: string;
  type: string;
}
export interface Site {
  ref: string;
  label: string;
  configured: boolean;
  tenant_id: string;
}
export interface Quota {
  configured: boolean;
  limit_mb: number | null;
  used_mb: number | null;
  percent: number | null;
}

export const MAX_UPLOAD = 10 * 1024 * 1024;
export const CHUNK = 512 * 1024;
export const BLOCKED = [".exe", ".dll", ".scr", ".msi", ".com", ".bat", ".cmd", ".so", ".dylib", ".jar", ".apk"];

export class AgentTimeout extends Error {
  constructor() {
    super("The server agent has not responded. The request is still queued; retry in a moment.");
    this.name = "AgentTimeout";
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Poll an operation until the agent finishes it. `onWaiting` fires while it is still queued. */
export async function waitJob<R>(id: string, scope: string, onWaiting?: (status: string) => void, timeoutMs = 60000): Promise<Job<R>> {
  const t0 = Date.now();
  for (;;) {
    const j = await get<Job<R>>(`/files/jobs/${id}`, scopeQuery(scope));
    if (j.status === "succeeded") {
      if (j.result?.ok === false || j.error) throw new ApiError(422, "agent_failed", friendly(j.error ?? "Operation failed"));
      return j;
    }
    if (j.status === "failed" || j.status === "timed_out" || j.status === "cancelled") throw new ApiError(422, "agent_failed", friendly(j.error ?? `Operation ${j.status}`));
    onWaiting?.(j.status);
    if (Date.now() - t0 > timeoutMs) throw new AgentTimeout();
    await sleep(700);
  }
}

/** scope = tenant_id for platform staff, "" for tenant users. */
export function scopeQuery(scope: string) {
  return scope ? { tenant_id: scope } : undefined;
}

export function friendly(msg: string): string {
  const m = msg.replace(/^[a-z_]+:\s*/, "");
  return m.charAt(0).toUpperCase() + m.slice(1);
}

export async function runOp<R = Record<string, unknown>>(site: string, scope: string, action: string, body: unknown, onWaiting?: (s: string) => void): Promise<Job<R>> {
  const q = scope ? `?tenant_id=${encodeURIComponent(scope)}` : "";
  const j = await api<Job<R>>(`/files/${encodeURIComponent(site)}/${action}${q}`, { method: "POST", body: body ?? {} });
  return waitJob<R>(j.id, scope, onWaiting);
}

export async function downloadFile(site: string, scope: string, path: string, name: string, onWaiting?: (s: string) => void) {
  const j = await runOp(site, scope, "read", { path }, onWaiting);
  const q = scope ? `?tenant_id=${encodeURIComponent(scope)}` : "";
  const res = await fetch(`/api/v1/files/jobs/${j.id}/download${q}`, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, "download_failed", "Download failed or was already delivered.");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export function checkUpload(f: File): string | null {
  const ext = f.name.includes(".") ? "." + f.name.split(".").pop()!.toLowerCase() : "";
  if (BLOCKED.includes(ext)) return `Files of type ${ext} are not allowed.`;
  if (f.size > MAX_UPLOAD) return `Uploads are limited to ${MAX_UPLOAD / 1024 / 1024} MB.`;
  return null;
}

function b64(buf: ArrayBuffer): string {
  let s = "";
  const u = new Uint8Array(buf);
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000));
  return btoa(s);
}

function uid(): string {
  const a = new Uint8Array(18);
  crypto.getRandomValues(a);
  return Array.from(a, (x) => "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"[x % 64]).join("");
}

export async function uploadFile(site: string, scope: string, dir: string, f: File, overwrite: boolean, onProgress: (pct: number) => void, onWaiting?: (s: string) => void) {
  const bad = checkUpload(f);
  if (bad) throw new ApiError(422, "client_policy", bad);
  const path = dir ? `${dir}/${f.name}` : f.name;
  const total = Math.max(1, Math.ceil(f.size / CHUNK));
  const id = uid();
  for (let i = 0; i < total; i++) {
    const buf = await f.slice(i * CHUNK, (i + 1) * CHUNK).arrayBuffer();
    await runOp(site, scope, "upload", { path, content_b64: b64(buf), upload_id: id, chunk_index: i, total_chunks: total, total_size: f.size, overwrite }, onWaiting);
    onProgress(Math.round(((i + 1) / total) * 100));
  }
}
