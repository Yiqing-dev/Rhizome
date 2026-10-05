// SPDX-License-Identifier: Apache-2.0
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import zh from "./locales/zh-CN.json";

const LANG_KEY = "rhizome.lang";

function initialLanguage(): string {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (saved) return saved;
  } catch {
    /* ignore */
  }
  return navigator.language.toLowerCase().startsWith("zh") ? "zh-CN" : "en";
}

i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, "zh-CN": { translation: zh } },
  lng: initialLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export function setLanguage(lng: "en" | "zh-CN"): void {
  i18n.changeLanguage(lng);
  document.documentElement.lang = lng;
  try {
    localStorage.setItem(LANG_KEY, lng);
  } catch {
    /* ignore */
  }
}

/** Adopt the language the backend resolved ("en" | "zh_CN"), without writing it back. */
export function applyBackendLanguage(backend: string): void {
  const lng = backend.startsWith("zh") ? "zh-CN" : "en";
  if (lng !== i18n.language) setLanguage(lng);
}

/** Backend language code for a UI language. */
export const backendLang = (lng: string) => (lng.startsWith("zh") ? "zh_CN" : "en");

/** The backend emits naive UTC timestamps (no offset); treat them as UTC, not local time. */
export const parseUtc = (iso: string): Date => (/(Z|[+-]\d\d:?\d\d)$/.test(iso) ? new Date(iso) : new Date(iso + "Z"));
export const fmtDate = (iso: string | null | undefined, lng: string) =>
  iso ? new Intl.DateTimeFormat(lng, { dateStyle: "medium" }).format(parseUtc(iso)) : "";
export const fmtNum = (n: number, lng: string) => new Intl.NumberFormat(lng).format(n);

export default i18n;
