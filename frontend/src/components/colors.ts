// SPDX-License-Identifier: Apache-2.0
// One colour per entity type and per edge type, read from the CSS tokens so graphs follow the theme.

export const ENTITY_TYPES = ["work", "dataset", "method", "idea", "claim", "topic", "organism", "modality"] as const;
export const EDGE_TYPES = [
  "about", "applicable_to", "proposes", "uses", "produces", "evaluates", "supports", "contradicts", "extends",
  "cites", "is_a", "of_organism", "of_modality", "relates_to",
] as const;

const EDGE_TOKEN: Record<string, string> = {
  about: "--c-topic", applicable_to: "--c-topic", proposes: "--c-idea", uses: "--c-dataset", produces: "--c-dataset",
  evaluates: "--c-method", supports: "--ok", contradicts: "--err", extends: "--c-method", cites: "--c-modality",
  is_a: "--c-work", of_organism: "--c-organism", of_modality: "--c-modality", relates_to: "--c-idea",
};

export function cssVar(name: string, fallback = "#888888"): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export const typeColor = (type: string): string => cssVar(`--c-${type}`);
export const edgeColor = (type: string): string => cssVar(EDGE_TOKEN[type] ?? "--line-strong");
/** applicable_to / cites are drawn dashed to distinguish them from their solid siblings. */
export const edgeDashed = (type: string): boolean => type === "applicable_to" || type === "cites";
export const ink = (): string => cssVar("--ink-2", "#444");
export const surface = (): string => cssVar("--surface", "#fff");

export const COMMUNITY_PALETTE = ["#4a68e6", "#178f6c", "#d2650b", "#7466b8", "#d4297f", "#5e9a1b", "#c99a06", "#9c6f1d", "#1f78b4", "#b04a8e"];
export const communityColor = (c: number | null | undefined): string =>
  c === null || c === undefined ? cssVar("--line-strong") : COMMUNITY_PALETTE[c % COMMUNITY_PALETTE.length];

/** Blend hex colour `c` toward `bg` by `amount` (0 = c, 1 = bg). Both must be #rrggbb. */
export function tint(c: string, bg: string, amount: number): string {
  const h = (x: string) => x.replace("#", "").padEnd(6, "0").slice(0, 6);
  const a = h(c), b = h(bg);
  if (a.length !== 6 || b.length !== 6) return c;
  const mix = (i: number) => Math.round(parseInt(a.slice(i, i + 2), 16) * (1 - amount) + parseInt(b.slice(i, i + 2), 16) * amount);
  return "#" + [0, 2, 4].map((i) => mix(i).toString(16).padStart(2, "0")).join("");
}
