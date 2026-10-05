// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useRef, useState } from "react";
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
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
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
