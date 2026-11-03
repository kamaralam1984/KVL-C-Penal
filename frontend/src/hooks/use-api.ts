"use client";

import * as React from "react";
import { ApiError, api, type ReqOpts } from "@/lib/http";

export interface ApiState<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

/** GET a resource and keep it in state. `path` may be null to skip fetching (e.g. waiting on an id). */
export function useApi<T>(path: string | null, query?: ReqOpts["query"], opts?: { refreshMs?: number }): ApiState<T> {
  const [data, setData] = React.useState<T>();
  const [error, setError] = React.useState<ApiError>();
  const [loading, setLoading] = React.useState(path !== null);
  const [tick, setTick] = React.useState(0);
  const key = path === null ? null : path + JSON.stringify(query ?? {});

  React.useEffect(() => {
    if (key === null || path === null) return;
    const ctl = new AbortController();
    let alive = true;
    api<T>(path, { query, signal: ctl.signal })
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError(undefined);
      })
      .catch((e) => {
        if (alive && e?.name !== "AbortError") setError(e instanceof ApiError ? e : new ApiError(0, "network", "Network error"));
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
      ctl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick]);

  React.useEffect(() => {
    if (!opts?.refreshMs || key === null) return;
    const id = setInterval(() => setTick((t) => t + 1), opts.refreshMs);
    return () => clearInterval(id);
  }, [opts?.refreshMs, key]);

  const reload = React.useCallback(() => {
    setLoading(true);
    setTick((t) => t + 1);
  }, []);
  return { data, error, loading: loading && data === undefined, reload };
}

/** Run a write call with loading + error state. Returns the result or undefined on failure. */
export function useMutation<A, R = unknown>(fn: (arg: A) => Promise<R>) {
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState<ApiError>();
  const run = React.useCallback(
    async (arg: A): Promise<R | undefined> => {
      setPending(true);
      setError(undefined);
      try {
        return await fn(arg);
      } catch (e) {
        setError(e instanceof ApiError ? e : new ApiError(0, "network", "Network error"));
        return undefined;
      } finally {
        setPending(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  return { run, pending, error, clearError: () => setError(undefined) };
}
