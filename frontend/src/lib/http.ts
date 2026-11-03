/** Browser HTTP client for the FastAPI control plane: cookies, CSRF header, one silent refresh on 401. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public fields?: { field: string; message: string }[],
    public extra?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const BASE = "/api/v1";

function csrf(): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/(?:^|; )kvl_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

let refreshing: Promise<boolean> | null = null;
async function refresh(): Promise<boolean> {
  refreshing ??= fetch(`${BASE}/auth/refresh`, { method: "POST", credentials: "include", headers: { "X-CSRF-Token": csrf() } })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      setTimeout(() => (refreshing = null), 0);
    });
  return refreshing;
}

export interface ReqOpts {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
  /** Skip the 401 → refresh → retry dance (used by auth endpoints themselves). */
  noRefresh?: boolean;
  headers?: Record<string, string>;
}

function url(path: string, query?: ReqOpts["query"]) {
  const q = query
    ? Object.entries(query)
        .filter(([, v]) => v !== undefined && v !== null && v !== "")
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
        .join("&")
    : "";
  return `${BASE}${path}${q ? `?${q}` : ""}`;
}

export async function api<T = unknown>(path: string, opts: ReqOpts = {}): Promise<T> {
  const method = opts.method ?? (opts.body !== undefined ? "POST" : "GET");
  const doFetch = () =>
    fetch(url(path, opts.query), {
      method,
      credentials: "include",
      signal: opts.signal,
      headers: {
        Accept: "application/json",
        ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" ? { "X-CSRF-Token": csrf() } : {}),
        ...opts.headers,
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });

  let res = await doFetch();
  if (res.status === 401 && !opts.noRefresh && !path.startsWith("/auth/login") && !path.startsWith("/auth/refresh")) {
    if (await refresh()) res = await doFetch();
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  const data = ct.includes("json") ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    const e = (data as { error?: { code?: string; message?: string; fields?: { field: string; message: string }[] } } | null)?.error;
    throw new ApiError(res.status, e?.code ?? "error", e?.message ?? res.statusText, e?.fields, e as Record<string, unknown>);
  }
  return data as T;
}

export const get = <T,>(path: string, query?: ReqOpts["query"]) => api<T>(path, { query });
export const post = <T,>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body ?? {} });
export const put = <T,>(path: string, body?: unknown) => api<T>(path, { method: "PUT", body: body ?? {} });
export const patch = <T,>(path: string, body?: unknown) => api<T>(path, { method: "PATCH", body: body ?? {} });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });
