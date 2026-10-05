// SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { errorText, type EntityType, type Hit, type Source } from "../api";
import { href } from "../router";
import { EDGE_TYPES, ENTITY_TYPES } from "./colors";
import { LeafIcon } from "./icons";

export function TypeBadge({ type }: { type: EntityType | string }) {
  const { t } = useTranslation();
  return <span className={`badge t-${type}`}>{t(`type.${type}`)}</span>;
}

export function entityHref(e: { id: number; type: string }): string {
  return e.type === "topic" ? href(`topic/${e.id}`) : href(`entity/${e.id}`);
}

export function EntityLink({ e, className }: { e: { id: number; type: string; name: string }; className?: string }) {
  return <a className={className} href={entityHref(e)}>{e.name}</a>;
}

export function Sources({ sources, max = 3 }: { sources: Source[]; max?: number }) {
  const { t } = useTranslation();
  if (!sources.length) return null;
  return (
    <div className="sources">
      {sources.slice(0, max).map((s, i) => (
        <span key={i}>
          <span className="muted">{t(`edge.${s.edge}`)}</span>{" "}
          <a href={href(`entity/${s.work_id}`)}>{s.title}</a>
          {s.year ? <span className="muted"> ({s.year})</span> : null}
          {s.evidence ? <> · <span className="evidence">{s.evidence}</span></> : null}
        </span>
      ))}
      {sources.length > max ? <span className="muted small">+{sources.length - max}</span> : null}
    </div>
  );
}

export function ScoreBar({ value, type }: { value: number | null | undefined; type?: string }) {
  if (value === null || value === undefined) return null;
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <span className={`score ${type ? `t-${type}` : ""}`} title={value.toFixed(3)}>
      <i style={{ "--w": `${pct}%` } as React.CSSProperties} />
      {value.toFixed(2)}
    </span>
  );
}

export function HitRow({ h, score }: { h: Hit; score?: number | null }) {
  const { t } = useTranslation();
  const shown = score ?? h.relevance ?? null;
  return (
    <li className={`hit t-${h.type}`}>
      <div className="title">
        <TypeBadge type={h.type} />
        <EntityLink e={h} />
        {h.status === "candidate" ? <span className="pill">{t("status.candidate")}</span> : null}
        {h.origin === "user" ? <span className="pill user">{t("origin.user")}</span> : null}
      </div>
      <Sources sources={h.sources} />
      {(shown !== null || h.external_id) && (
        <div className="meta">
          {h.external_id ? <code>{h.external_id}</code> : null}
          <ScoreBar value={shown} type={h.type} />
        </div>
      )}
    </li>
  );
}

export function Loading({ error, loading }: { error: unknown; loading: boolean }) {
  const { t } = useTranslation();
  if (error) {
    return <div className="notice error"><pre>{errorText(error, t)}</pre></div>;
  }
  return loading ? <div className="muted">{t("common.loading")}</div> : null;
}

export function EmptyState({ title, hint, icon, children }: { title: string; hint?: string; icon?: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty">
      {icon ?? <LeafIcon />}
      <b>{title}</b>
      {hint ? <span className="small">{hint}</span> : null}
      {children}
    </div>
  );
}

/** Colour legend for graph views: node types and edge types that actually occur. */
export function Legend({ nodeTypes, edgeTypes }: { nodeTypes?: Iterable<string>; edgeTypes?: Iterable<string> }) {
  const { t } = useTranslation();
  const nodes = ENTITY_TYPES.filter((x) => !nodeTypes || new Set(nodeTypes).has(x));
  const edges = EDGE_TYPES.filter((x) => !edgeTypes || new Set(edgeTypes).has(x));
  return (
    <div className="legend">
      {nodes.map((n) => (
        <span key={n} className={`t-${n}`}><i className="node" style={{ background: `var(--c-${n})` }} />{t(`type.${n}`)}</span>
      ))}
      {edges.map((e) => (
        <span key={e}><i style={{ background: edgeToken(e) }} />{t(`edge.${e}`)}</span>
      ))}
    </div>
  );
}

function edgeToken(e: string): string {
  const map: Record<string, string> = {
    about: "var(--c-topic)", applicable_to: "var(--c-topic)", proposes: "var(--c-idea)", uses: "var(--c-dataset)",
    produces: "var(--c-dataset)", evaluates: "var(--c-method)", supports: "var(--ok)", contradicts: "var(--err)",
    extends: "var(--c-method)", cites: "var(--c-modality)", is_a: "var(--c-work)", of_organism: "var(--c-organism)",
    of_modality: "var(--c-modality)", relates_to: "var(--c-idea)",
  };
  return map[e] ?? "var(--line-strong)";
}

export { edgeToken };
