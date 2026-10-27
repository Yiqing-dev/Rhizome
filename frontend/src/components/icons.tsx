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

/** The Modernist brand mark: a 2×2 grid of squares, the first in the accent. */
export const Mark = () => (
  <span className="mark" aria-hidden="true"><i /><i /><i /><i /></span>
);

// Lucide outlines (https://lucide.dev), as the Modernist design system prescribes
const lucide = { ...base, strokeWidth: 2 } as const;
export const SearchIcon = () => (
  <svg viewBox="0 0 24 24" {...lucide}><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></svg>
);
export const UploadIcon = () => (
  <svg viewBox="0 0 24 24" {...lucide}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" x2="12" y1="3" y2="15" /></svg>
);
export const SparkIcon = () => (
  <svg viewBox="0 0 24 24" {...lucide}><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" /></svg>
);
export const ArrowIcon = () => (
  <svg viewBox="0 0 24 24" {...lucide}><path d="M5 12h14" /><path d="m12 5 7 7-7 7" /></svg>
);
export const ChevronIcon = () => (
  <svg viewBox="0 0 24 24" {...lucide}><path d="m6 9 6 6 6-6" /></svg>
);
export const FolderIcon = () => (
  <svg viewBox="0 0 24 24" {...lucide}><path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2" /></svg>
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
