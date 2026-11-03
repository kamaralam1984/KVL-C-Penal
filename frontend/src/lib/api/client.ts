/**
 * Typed boundary for the FastAPI control plane (built in later phases).
 * Phase 1 has no backend: nothing calls this yet. It exists so screens depend on
 * a stable seam instead of on fixtures directly.
 */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1";

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, { ...init, headers: { Accept: "application/json", ...init?.headers } });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { code?: string; message?: string } | null;
    throw new ApiError(res.status, body?.code ?? "unknown_error", body?.message ?? res.statusText);
  }
  return (await res.json()) as T;
}
