import * as React from "react";

const listeners = new Set<() => void>();

/** Persisted boolean preference without effect-driven state sync (SSR-safe: server snapshot is `fallback`). */
export function useLocalFlag(key: string, fallback = false): [boolean, (v: boolean) => void] {
  const subscribe = React.useCallback((cb: () => void) => {
    listeners.add(cb);
    return () => void listeners.delete(cb);
  }, []);
  const get = React.useCallback(() => {
    try {
      const v = window.localStorage.getItem(key);
      return v === null ? fallback : v === "1";
    } catch {
      return fallback;
    }
  }, [key, fallback]);
  const value = React.useSyncExternalStore(subscribe, get, () => fallback);
  const set = React.useCallback(
    (v: boolean) => {
      try {
        window.localStorage.setItem(key, v ? "1" : "0");
      } catch {
        /* storage unavailable: preference simply isn't persisted */
      }
      listeners.forEach((l) => l());
    },
    [key],
  );
  return [value, set];
}
