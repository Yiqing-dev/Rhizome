// SPDX-License-Identifier: Apache-2.0
// One import at a time, owned by the server (a worker job; progress in the library), so the
// progress survives a page reload, a minimised window or an app restart. While it runs the whole
// app is locked: no clicks, no keyboard shortcuts, no page changes.
import { useSyncExternalStore } from "react";
import { api, ApiError, apiUrl, authHeader, changed, type ImportProgress } from "./api";

export interface ImportState {
  uploading: { loaded: number; total: number } | null; // files on their way to the server
  progress: ImportProgress | null; // the server-side batch, until it is dismissed
  minimized: boolean;
  error: string | null; // the upload itself failed
}

let state: ImportState = { uploading: null, progress: null, minimized: false, error: null };
const listeners = new Set<() => void>();
let timer = 0;
let lockedHash: string | null = null;

function set(patch: Partial<ImportState>) {
  state = { ...state, ...patch };
  applyLock();
  listeners.forEach((l) => l());
}

export function isLocked(s: ImportState = state): boolean {
  return !!s.uploading || !!s.progress;
}

function running(p: ImportProgress | null): boolean {
  return !!p && (p.status === "queued" || p.status === "running");
}

/** While locked: the page stays where the import started, shortcuts are off. */
function applyLock() {
  const locked = isLocked();
  document.body.dataset.importing = locked ? "1" : "";
  if (locked && lockedHash === null) lockedHash = window.location.hash;
  if (!locked) lockedHash = null;
}

window.addEventListener("hashchange", () => {
  if (lockedHash !== null && window.location.hash !== lockedHash) window.location.hash = lockedHash;
});
window.addEventListener("beforeunload", (e) => {
  if (state.uploading) { e.preventDefault(); e.returnValue = ""; } // the upload would be lost; the job would not
});
document.addEventListener("visibilitychange", () => { if (!document.hidden && state.progress) void refresh(); });

export async function refresh(): Promise<void> {
  window.clearTimeout(timer);
  try {
    const { active } = await api.importActive();
    const wasRunning = running(state.progress);
    set({ progress: active });
    if (wasRunning && !running(active)) changed(); // the library grew: counters and lists reload
  } catch {
    /* backend restarting: try again shortly */
  }
  if (running(state.progress) || (state.progress === null && state.uploading)) {
    timer = window.setTimeout(() => void refresh(), 1000);
  }
}

export function start(files: File[]): void {
  if (isLocked() || !files.length) return;
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", apiUrl("/import"));
  xhr.setRequestHeader("Authorization", authHeader());
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) set({ uploading: { loaded: e.loaded, total: e.total } }); };
  xhr.onload = () => {
    let data: any = null;
    try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
    if (xhr.status >= 200 && xhr.status < 300) {
      set({ uploading: null, progress: data as ImportProgress, minimized: false, error: null });
      void refresh();
    } else {
      const d = data?.detail;
      set({ uploading: null, error: d?.code === "not_rxf" ? "not_rxf" : typeof d === "string" ? d : `HTTP ${xhr.status}` });
      if (xhr.status === 409) void refresh(); // another import is active: show it
    }
  };
  xhr.onerror = () => set({ uploading: null, error: "unreachable" });
  set({ uploading: { loaded: 0, total: files.reduce((n, f) => n + f.size, 0) }, error: null, minimized: false });
  xhr.send(form);
}

export async function repair(): Promise<void> {
  if (!state.progress) return;
  try {
    set({ progress: await api.importRepair(state.progress.batch) });
  } catch (e) {
    set({ error: e instanceof ApiError ? String(e.detail) : String(e) });
  }
  void refresh();
}

export async function dismiss(): Promise<void> {
  if (!state.progress || running(state.progress)) return;
  try {
    await api.importDismiss(state.progress.batch);
  } catch {
    /* already gone */
  }
  set({ progress: null, minimized: false });
}

export function clearError(): void {
  set({ error: null });
}

export function setMinimized(minimized: boolean): void {
  set({ minimized });
}

export function useImport(): ImportState {
  return useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l); }, () => state);
}
