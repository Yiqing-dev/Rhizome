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
    history.replaceState(null, "", "#/"); // not a new history entry: Back must not return to #token=
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

/** Readable text for any error: FastAPI 422 lists, {report} / {detail} objects, plain strings,
 * an unreachable backend. `t` translates the generic cases. */
export function errorText(e: unknown, t: (k: string) => string): string {
  if (!(e instanceof ApiError)) return e instanceof Error ? e.message : String(e);
  if (e.status === 0) return t("common.unreachable");
  if (e.status === 401) return t("common.unauthorized");
  const d = e.detail as any;
  if (typeof d === "string" && d.trim()) return d;
  if (Array.isArray(d)) return d.map((x) => (x?.loc ? `${x.loc.join(".")}: ` : "") + (x?.msg ?? JSON.stringify(x))).join("\n");
  if (d && typeof d === "object") return d.report ?? d.detail ?? d.error ?? JSON.stringify(d);
  return e.status === 404 ? t("common.not_found") : `${t("common.error")} (HTTP ${e.status})`;
}

async function req<T>(method: string, path: string, body?: unknown, params?: Record<string, unknown>): Promise<T> {
  const url = new URL(BASE + path, window.location.origin);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: { Authorization: `Bearer ${token()}`, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, null); // the backend is not reachable (stopped, restarting)
  }
  const ct = res.headers.get("content-type") ?? "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, (data as { detail?: unknown })?.detail ?? data);
  if (method !== "GET") changed(); // counters and lists elsewhere on the page refresh themselves
  return data as T;
}

/** Tell the open pages that the library changed (also dispatched by the file upload below). */
export function changed(): void {
  window.dispatchEvent(new Event("rhz:changed"));
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
  user_insights: { text: string; links_to: string[]; key?: string; entity_id?: number; name?: string; edited?: boolean }[];
  suspect: string[]; raw: string | null; has_pdf: boolean;
}
export interface Card extends Hit {
  attrs: Record<string, any>; aliases: { alias: string; lang: string; source: string }[];
  edges: Record<string, EdgeView[]>; edge_counts?: Record<string, number>; created_at: string;
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
  totals: Record<"dataset" | "method" | "idea" | "claim" | "work", number>; limit: number;
}
export interface ReviewItem {
  id: number; kind: string; payload: Record<string, any>; score: number; actions: string[]; created_at: string;
  context: Record<string, { id: number; name: string; type: string; aliases: string[]; definition?: string;
    connections: { type: string; direction: string; name: string }[] }>;
}
export interface DueCard { id: string; q: string; a: string; entity_key: string; entity_name: string | null; origin: string; new: boolean }
export interface Stats { entities: Record<string, number>; edges: number; review_queue: number; review_queue_by_kind: Record<string, number>; cards_due: number;
  inbox?: { watching: boolean; last_scan: string | null; pending: string[]; ignored: string[] };
  update?: { latest: string; url: string | null; current: string } | null }

export interface Decision { id: number; op: string; payload: Record<string, unknown>; created_at: string; revoked_at: string | null }
export interface JobView {
  id: number; kind: string; status: "queued" | "running" | "done" | "failed"; payload: Record<string, unknown>;
  result: Record<string, any> | null; error: string | null; created_at: string | null; started_at: string | null; finished_at: string | null;
}
export interface FailedFile { name: string; report: string | null; repairable: string[]; has_pdf: boolean; modified: string }

export interface SystemInfo {
  frozen: boolean; platform: string; app_dir: string | null; portable: boolean; data_dir: string; default_data_dir: string;
  inbox: string; logs: string; models_dir: string; models_available: boolean; local_llm_available: boolean;
  cli: string[]; claude_desktop: Record<string, unknown>; claude_config_paths: string[];
  backups: BackupStatus;
  model_problems: { kind: string; model: string; missing: string }[];
  index: { model: string | null; current: string | null; stale: boolean };
  network: { offline: boolean; hosts: Record<string, { ok: number; failed: number; last_error?: string }> };
}
export interface BackupStatus { dir: string; count: number; bytes: number; last: string | null; last_daily: string | null; error?: string }

export const api = {
  health: () => req<{ ok: boolean; version: string; read_only: boolean; moved_to?: string | null; language: string }>("GET", "/health"),
  stats: () => req<Stats>("GET", "/stats"),
  search: (q: string, f: Record<string, unknown> = {}) =>
    req<{ results: Hit[]; has_more?: boolean; total?: number; offset?: number }>("GET", "/search", undefined, { q, ...f }),
  entity: (id: number) => req<Card>("GET", `/entity/${id}`),
  neighbors: (id: number, hops = 1, offset = 0) => req<GraphView>("GET", `/entity/${id}/neighbors`, undefined, { hops, offset }),
  related: (id: number) => req<{ related: any[] }>("GET", `/entity/${id}/related`),
  topicAssets: (id: number, role?: string, column?: string, offset = 0) =>
    req<TopicPage>("GET", `/topic/${id}/assets`, undefined, { role, column, offset: offset || undefined }),
  entityEdges: (id: number, type: string, offset: number, limit = 100) =>
    req<{ edges: EdgeView[]; total: number }>("GET", `/entity/${id}/edges`, undefined, { type, offset, limit }),
  topicMap: (includeCandidates = false) => req<TopicMap>("GET", "/topic-map", undefined, { include_candidates: includeCandidates }),
  createTopic: (b: { name: string; definition?: string; examples?: string[]; counter_examples?: string[]; parent?: string }) =>
    req<{ topic_id: number; job_id: number | null }>("POST", "/topic", b),
  review: (kind?: string, offset = 0) => req<{ items: ReviewItem[]; total: number }>("GET", "/review", undefined, { kind, offset, limit: 50 }),
  resolve: (id: number, action: string, note?: string) => req<{ status: string }>("POST", `/review/${id}`, { action, note }),
  decide: (op: string, payload: Record<string, unknown>) => req<{ id: number; rebuild_job: number | null }>("POST", "/decision", { op, payload }),
  decisions: (limit = 50, offset = 0) => req<{ decisions: Decision[] }>("GET", "/decisions", undefined, { limit, offset }),
  revoke: (id: number) => req<{ revoked: number; changed: boolean; rebuild_job: number | null }>("DELETE", `/decision/${id}`),
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
    changed();
    return data;
  },
  inboxFailed: () => req<{ files: FailedFile[] }>("GET", "/inbox/failed"),
  inboxRetry: (name: string, repair: boolean) => req<any>("POST", `/inbox/failed/${encodeURIComponent(name)}`, { repair }),
  recall: (text: string) => req<{ results: Hit[] }>("POST", "/recall", { text }),
  cardsDue: () => req<{ cards: DueCard[] }>("GET", "/cards/due"),
  grade: (id: string, rating: number) => req<{ interval_days: number; due: string }>("POST", `/cards/${id}/grade`, { rating }),
  suspendCard: (id: string, entity = false) => req<{ suspended: boolean; cards: number }>("POST", `/cards/${id}/suspend`, { suspended: true, entity }),
  dismissReview: (kind: string, topic?: string) => req<{ dismissed: number }>("POST", "/review/dismiss", { kind, topic }),
  digest: () => req<any>("GET", "/digest"),
  settings: () => req<Record<string, any>>("GET", "/settings"),
  patchSettings: (b: Record<string, unknown>) => req<Record<string, any>>("PATCH", "/settings", b),
  vocab: (includeCandidates = true) => req<string>("GET", "/vocab", undefined, { include_candidates: includeCandidates }),
  rxfInstructions: (lang: string) => req<string>("GET", "/rxf/instructions", undefined, { lang }),
  system: () => req<SystemInfo>("GET", "/system"),
  openFolder: (target: "data" | "inbox" | "logs" | "models" | "backups") => req<{ opened: string }>("POST", `/system/open/${target}`),
  backupNow: () => req<BackupStatus & { path: string | null }>("POST", "/system/backup"),
  restart: () => req<{ restarting: boolean }>("POST", "/system/restart"),
  connectClaude: () => req<{ written: string[]; entry: Record<string, unknown> }>("POST", "/system/claude-desktop"),
  moveDataDir: (path: string | null, copy = true) =>
    req<{ data_dir: string; copied: boolean; restart_required: boolean; claude_config_updated?: string[] }>("POST", "/system/data-dir", { path, copy }),
  runJob: (kind: string, payload: Record<string, unknown> = {}) => req<JobView>("POST", "/jobs", { kind, payload }),
  resetSynthesisThreshold: () => req<{ threshold: number }>("DELETE", "/synthesis/threshold"),
  job: (id: number) => req<JobView>("GET", `/jobs/${id}`),
  activeJobs: () => req<{ jobs: JobView[]; last_failed: JobView | null }>("GET", "/jobs", undefined, { active: true }),
};
