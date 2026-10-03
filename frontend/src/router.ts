// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";

export interface Route { path: string[]; query: URLSearchParams }

function parse(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [p, q] = raw.split("?");
  return { path: p.split("/").filter(Boolean), query: new URLSearchParams(q ?? "") };
}

export function useRoute(): Route {
  const [r, setR] = useState(parse);
  useEffect(() => {
    const on = () => setR(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return r;
}

export function go(path: string, query?: Record<string, string | number | undefined>): void {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined && v !== "") qs.set(k, String(v));
  window.location.hash = `#/${path}${qs.toString() ? "?" + qs : ""}`;
}

export const href = (path: string) => `#/${path}`;
