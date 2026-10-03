// SPDX-License-Identifier: Apache-2.0
// Tiny inline icon set (stroke icons, currentColor).

// 16px by default; CSS may enlarge (e.g. .drop svg, .empty svg)
const base = { width: 16, height: 16, fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;

export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32">
      <circle cx="16" cy="16" r="6" fill="var(--c-topic)" />
      <path d="M16 10 L9 4 M16 22 L22 28 M10 16 L3 18 M22 16 L29 13" stroke="var(--c-topic)" strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="9" cy="4" r="2.6" fill="var(--c-work)" />
      <circle cx="22" cy="28" r="2.6" fill="var(--c-method)" />
      <circle cx="3" cy="18" r="2.6" fill="var(--c-idea)" />
      <circle cx="29" cy="13" r="2.6" fill="var(--c-dataset)" />
    </svg>
  );
}

export const SearchIcon = () => (
  <svg viewBox="0 0 24 24" {...base}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
);
export const UploadIcon = () => (
  <svg viewBox="0 0 24 24" {...base}><path d="M12 16V4m0 0 4 4m-4-4-4 4" /><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" /></svg>
);
export const SparkIcon = () => (
  <svg viewBox="0 0 24 24" {...base}><path d="M12 3v4m0 10v4M3 12h4m10 0h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" /></svg>
);
export const GraphIcon = () => (
  <svg viewBox="0 0 24 24" {...base}><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="8" r="2.5" /><circle cx="12" cy="18" r="2.5" /><path d="M8 7.5 15.5 8.5M7.5 8 11 15.5M16.5 10.2 13 15.8" /></svg>
);
export const LeafIcon = () => (
  <svg viewBox="0 0 24 24" {...base}><path d="M5 19c0-8 5-13 14-14-1 9-6 14-14 14Z" /><path d="M5 19c3-5 6-8 10-10" /></svg>
);
export const CheckIcon = () => (
  <svg viewBox="0 0 24 24" {...base}><path d="m5 12 4 4L19 6" /></svg>
);
export const CardIcon = () => (
  <svg viewBox="0 0 24 24" {...base}><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M8 10h8M8 14h5" /></svg>
);
export const LinkIcon = () => (
  <svg viewBox="0 0 24 24" {...base}><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7L11.5 6.8" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.5-1.5" /></svg>
);
export const SettingsIcon = () => (
  <svg viewBox="0 0 24 24" {...base}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" /></svg>
);
