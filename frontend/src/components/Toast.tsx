// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";

type Toast = { id: number; text: string; kind: "error" | "ok" };
let next = 1;
const listeners = new Set<(t: Toast[]) => void>();
let toasts: Toast[] = [];

function emit() {
  listeners.forEach((l) => l(toasts));
}

/** Show a short message in the corner (errors stay 10 s, others 4 s). */
export function showToast(text: string, kind: "error" | "ok" = "error"): void {
  const toast = { id: next++, text, kind };
  toasts = [...toasts.slice(-3), toast];
  emit();
  setTimeout(() => {
    toasts = toasts.filter((x) => x.id !== toast.id);
    emit();
  }, kind === "error" ? 10000 : 4000);
}

export function Toasts() {
  const [items, setItems] = useState<Toast[]>(toasts);
  useEffect(() => {
    listeners.add(setItems);
    return () => { listeners.delete(setItems); };
  }, []);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((x) => (
        <div key={x.id} className={`notice ${x.kind} toast`}>
          <pre>{x.text}</pre>
          <button className="link" onClick={() => navigator.clipboard?.writeText(x.text).catch(() => undefined)}>⧉</button>
        </div>
      ))}
    </div>
  );
}
