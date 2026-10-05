// SPDX-License-Identifier: Apache-2.0
// Thin REST client. The token arrives once in the URL fragment (#token=...) from `rhz serve` or
// the desktop shell and is kept in sessionStorage.

const BASE: string = import.meta.env.VITE_API_URL ?? "";
const KEY = "rhizome.token";

export function captureToken(): void {
  const m = window.location.hash.match(/token=([^&]+)/);
  if (m) {
    try {
      sessionStorage.setItem(KEY, decodeURIComponent(m[1]));
    } catch {
      /* storage unavailable: token lives for this page only */
      (window as unknown as { __rhzToken?: string }).__rhzToken = decodeURIComponent(m[1]);
    }
    window.location.hash = "#/";
  }
}

function token(): string {
  try {
    return sessionStorage.getItem(KEY) ?? (window as unknown as { __rhzToken?: string }).__rhzToken ?? "";
  } catch {
    return (window as unknown as { __rhzToken?: string }).__rhzToken ?? "";
  }
}

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}

async function req<T>(method: string, path: string, body?: unknown, params?: Record<string, unknown>): Promise<T> {
  const url = new URL(BASE + path, window.location.origin);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  const res = await fetch(url, {
    method,
    headers: { Authorization: `Bearer ${token()}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const ct = res.headers.get("content-type") ?? "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, (data as { detail?: unknown })?.detail ?? data);
  return data as T;
}

export type EntityType = "work" | "dataset" | "method" | "idea" | "claim" | "topic" | "organism" | "modality";

export interface Source { work_id: number; title: string; year: number | null; edge: string; evidence: string | null }
export interface Hit {
  id: number; key: string; type: EntityType; name: string; status: string; external_id: string | null;
  score: number | null; origin?: string | null; sources: Source[]; relevance?: number | null;
}
export interface EdgeView {
  id: number; type: string; direction: "in" | "out"; status: string; confidence: number; evidence: string | null;
  attrs: Record<string, unknown>; other: { id: number; key: string; type: EntityType; name: string; status: string };
}
export interface ExportView {
  extraction_id: number; depth: string; prompt_version: string | null; created_at: string; tldr: string[];
  claims: { text: string; evidence_type: string; evidence: string; boundary?: string; logic_jump?: boolean }[];
  issues: { severity: string; location?: string; text: string; test?: string }[];
  user_insights: { text: string; links_to: string[] }[]; suspect: string[]; raw: string | null; has_pdf: boolean;
}
export interface Card extends Hit {
  attrs: Record<string, any>; aliases: { alias: string; lang: string; source: string }[];
  edges: Record<string, EdgeView[]>; created_at: string;
  work?: { openalex_id: string | null; dois: string[]; year: number | null; tier: number } | null;
  exports?: ExportView[];
}
export interface GraphView {
  nodes: { id: number; key: string; type: EntityType; name: string; status: string }[];
  edges: { id: number; src: number; dst: number; type: string; status: string }[];
  total_nodes: number; offset: number; has_more: boolean;
}
export interface TopicMap {
  nodes: { id: number; key: string; name: string; status: string; counts: Record<string, number>; size: number; community: number | null }[];
  edges: { src: number; dst: number; status: string }[]; total_topics: number;
}
export interface TopicItem extends Hit { roles: string[]; works: number; contested: boolean; year: number | null }
export interface TopicPage {
  topic: Hit & { attrs: Record<string, any> }; children: Hit[]; parents: Hit[]; subtree_size: number;
  columns: Record<"dataset" | "method" | "idea" | "claim" | "work", TopicItem[]>;
}
export interface ReviewItem {
  id: number; kind: string; payload: Record<string, any>; score: number; actions: string[]; created_at: string;
  context: Record<string, { id: number; name: string; type: string; aliases: string[]; definition?: string;
    connections: { type: string; direction: string; name: string }[] }>;
}
export interface DueCard { id: string; q: string; a: string; entity_key: string; entity_name: string | null; origin: string; new: boolean }
export interface Stats { entities: Record<string, number>; edges: number; review_queue: number; review_queue_by_kind: Record<string, number>; cards_due: number }

export interface FailedFile { name: string; report: string | null; repairable: string[]; has_pdf: boolean; modified: string }

export interface SystemInfo {
  frozen: boolean; platform: string; app_dir: string | null; portable: boolean; data_dir: string; default_data_dir: string;
  inbox: string; logs: string; models_dir: string; models_available: boolean; local_llm_available: boolean;
  cli: string[]; claude_desktop: Record<string, unknown>; claude_config_paths: string[];
  backups: BackupStatus;
  model_problems: { kind: string; model: string; missing: string }[];
}
export interface BackupStatus { dir: string; count: number; bytes: number; last: string | null; last_daily: string | null; error?: string }

export const api = {
  health: () => req<{ ok: boolean; version: string; read_only: boolean; language: string }>("GET", "/health"),
  stats: () => req<Stats>("GET", "/stats"),
  search: (q: string, f: Record<string, unknown> = {}) => req<{ results: Hit[] }>("GET", "/search", undefined, { q, ...f }),
  entity: (id: number) => req<Card>("GET", `/entity/${id}`),
  neighbors: (id: number, hops = 1, offset = 0) => req<GraphView>("GET", `/entity/${id}/neighbors`, undefined, { hops, offset }),
  related: (id: number) => req<{ related: any[] }>("GET", `/entity/${id}/related`),
  topicAssets: (id: number, role?: string) => req<TopicPage>("GET", `/topic/${id}/assets`, undefined, { role }),
  topicMap: (includeCandidates = false) => req<TopicMap>("GET", "/topic-map", undefined, { include_candidates: includeCandidates }),
  createTopic: (b: { name: string; definition?: string; examples?: string[]; counter_examples?: string[]; parent?: string }) =>
    req<{ topic_id: number; job_id: number | null }>("POST", "/topic", b),
  review: (kind?: string, offset = 0) => req<{ items: ReviewItem[]; total: number }>("GET", "/review", undefined, { kind, offset, limit: 50 }),
  resolve: (id: number, action: string, note?: string) => req<{ status: string }>("POST", `/review/${id}`, { action, note }),
  decide: (op: string, payload: Record<string, unknown>) => req<{ id: number }>("POST", "/decision", { op, payload }),
  ingest: (text: string, filename: string) => req<any>("POST", "/ingest", { text, filename }),
  ingestFile: async (file: File, pdf?: File, repair = false) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (pdf) form.append("pdf", pdf, pdf.name);
    form.append("repair", String(repair));
    const res = await fetch(new URL(BASE + "/ingest", window.location.origin), {
      method: "POST", headers: { Authorization: `Bearer ${token()}` }, body: form,
    });
    const data = (res.headers.get("content-type") ?? "").includes("json") ? await res.json() : await res.text();
    if (!res.ok) throw new ApiError(res.status, (data as { detail?: unknown })?.detail ?? data);
    return data;
  },
  inboxFailed: () => req<{ files: FailedFile[] }>("GET", "/inbox/failed"),
  inboxRetry: (name: string, repair: boolean) => req<any>("POST", `/inbox/failed/${encodeURIComponent(name)}`, { repair }),
  recall: (text: string) => req<{ results: Hit[] }>("POST", "/recall", { text }),
  cardsDue: () => req<{ cards: DueCard[] }>("GET", "/cards/due"),
  grade: (id: string, rating: number) => req<{ interval_days: number }>("POST", `/cards/${id}/grade`, { rating }),
  digest: () => req<any>("GET", "/digest"),
  settings: () => req<Record<string, any>>("GET", "/settings"),
  patchSettings: (b: Record<string, unknown>) => req<Record<string, any>>("PATCH", "/settings", b),
  vocab: () => req<string>("GET", "/vocab"),
  rxfInstructions: (lang: string) => req<string>("GET", "/rxf/instructions", undefined, { lang }),
  system: () => req<SystemInfo>("GET", "/system"),
  openFolder: (target: "data" | "inbox" | "logs" | "models" | "backups") => req<{ opened: string }>("POST", `/system/open/${target}`),
  backupNow: () => req<BackupStatus & { path: string | null }>("POST", "/system/backup"),
  connectClaude: () => req<{ written: string[]; entry: Record<string, unknown> }>("POST", "/system/claude-desktop"),
  moveDataDir: (path: string | null, copy = true) =>
    req<{ data_dir: string; copied: boolean; restart_required: boolean; claude_config_updated?: string[] }>("POST", "/system/data-dir", { path, copy }),
  runJob: (kind: string) => req<{ id: number }>("POST", "/jobs", { kind, payload: {} }),
};
