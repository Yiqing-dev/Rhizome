// SPDX-License-Identifier: Apache-2.0
import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, type JobView } from "./api";

export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): {
  data: T | null; error: unknown; loading: boolean; reload: () => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn()
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e) => alive && setError(e))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((x) => x + 1), []);
  return { data, error, loading, reload };
}

export function useKeys(handler: (e: KeyboardEvent) => void, deps: unknown[]): void {
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (document.body.dataset.importing === "1") return;  // an import holds the app
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      // a held key, an IME composition or a browser shortcut (Ctrl+1) must not grade or decide
      if (e.repeat || e.isComposing || e.ctrlKey || e.metaKey || e.altKey) return;
      handler(e);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

/** Start a background job and follow it until it is done or failed (the button stays disabled
 * meanwhile, so repeated clicks don't queue the same work again). */
export function useJob(kind: string, onDone?: (j: JobView) => void, payload?: Record<string, unknown>): {
  job: JobView | null; running: boolean; start: () => Promise<void>;
} {
  const [job, setJob] = useState<JobView | null>(null);
  const timer = useRef<number | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const follow = useCallback((j: JobView) => {
    setJob(j);
    if (j.status === "queued" || j.status === "running") {
      timer.current = window.setTimeout(() => api.job(j.id).then(follow).catch(() => undefined), 1500);
    } else {
      done.current?.(j);
    }
  }, []);
  const body = JSON.stringify(payload ?? {});
  const start = useCallback(async () => follow(await api.runJob(kind, JSON.parse(body))), [kind, body, follow]);
  return { job, running: !!job && (job.status === "queued" || job.status === "running"), start };
}

/** Re-run `reload` when the window comes back (another window, Claude or the inbox may have
 * changed the library meanwhile) and when this app reports a change of its own ("rhz:changed",
 * dispatched by api.ts after every write). */
export function useRefreshOnFocus(reload: () => void): void {
  useEffect(() => {
    let last = Date.now();
    const soft = () => {
      if (document.visibilityState === "visible" && Date.now() - last > 2000) { last = Date.now(); reload(); }
    };
    const hard = () => { last = Date.now(); reload(); };
    window.addEventListener("focus", soft);
    document.addEventListener("visibilitychange", soft);
    window.addEventListener("rhz:changed", hard);
    return () => {
      window.removeEventListener("focus", soft);
      document.removeEventListener("visibilitychange", soft);
      window.removeEventListener("rhz:changed", hard);
    };
  }, [reload]);
}

/** Guard an async action so a double key press or click runs it once at a time. */
export function useBusy(): [React.MutableRefObject<boolean>, <T>(fn: () => Promise<T>) => Promise<T | undefined>] {
  const busy = useRef(false);
  const run = useCallback(async <T,>(fn: () => Promise<T>) => {
    if (busy.current) return undefined;
    busy.current = true;
    try {
      return await fn();
    } finally {
      busy.current = false;
    }
  }, []);
  return [busy, run];
}
