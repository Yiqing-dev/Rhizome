// SPDX-License-Identifier: Apache-2.0
// Appearance: light / dark / follow the system, and an accent colour. Applied as attributes on
// <html> (the CSS tokens key off them). localStorage is only the first-paint guess (index.html reads
// it before the bundle loads); the backend's settings are the record, as for the language, because
// the desktop app has a new origin on every launch.

export type Theme = "system" | "light" | "dark";
export const THEMES: Theme[] = ["system", "light", "dark"];
/** Preset name -> the base colour the tokens derive light and dark shades from. */
export const ACCENTS: Record<string, string> = {
  green: "#3e7d3b", teal: "#1f7f86", blue: "#2f62c9", violet: "#6a4fc2", rose: "#c23a6a",
  amber: "#b26a0b", graphite: "#4f5560",
};
const THEME_KEY = "rhizome.theme";
const ACCENT_KEY = "rhizome.accent";

export function currentTheme(): Theme {
  const v = document.documentElement.dataset.theme;
  return v === "light" || v === "dark" ? v : "system";
}
export function currentAccent(): string {
  return document.documentElement.dataset.accent || "green";
}

export function applyAppearance(theme: Theme, accent: string, remember = true): void {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme; else root.dataset.theme = theme;
  root.dataset.accent = accent in ACCENTS ? accent : "green";
  if (remember) {
    try {
      localStorage.setItem(THEME_KEY, theme);
      localStorage.setItem(ACCENT_KEY, root.dataset.accent);
    } catch { /* ignore */ }
  }
}

/** Adopt what the backend has (after /health), without writing it back. */
export function applyBackendAppearance(theme?: string | null, accent?: string | null): void {
  const t = theme === "light" || theme === "dark" ? theme : "system";
  applyAppearance(t, accent ?? currentAccent());
}
