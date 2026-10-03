// SPDX-License-Identifier: Apache-2.0
import { useTranslation } from "react-i18next";
import type { EntityType, Hit, Source } from "../api";
import { href } from "../router";

export function TypeBadge({ type }: { type: EntityType | string }) {
  const { t } = useTranslation();
  return <span className={`badge t-${type}`}>{t(`type.${type}`)}</span>;
}

export function entityHref(e: { id: number; type: string }): string {
  return e.type === "topic" ? href(`topic/${e.id}`) : href(`entity/${e.id}`);
}

export function EntityLink({ e }: { e: { id: number; type: string; name: string } }) {
  return <a href={entityHref(e)}>{e.name}</a>;
}

export function Sources({ sources }: { sources: Source[] }) {
  const { t } = useTranslation();
  if (!sources.length) return null;
  return (
    <div className="sources">
      {sources.slice(0, 3).map((s, i) => (
        <span key={i}>
          <span className="muted">{t(`edge.${s.edge}`)}</span>{" "}
          <a href={href(`entity/${s.work_id}`)}>{s.title}</a>
          {s.year ? <span className="muted"> ({s.year})</span> : null}
          {s.evidence ? <span className="evidence"> · {s.evidence}</span> : null}
        </span>
      ))}
    </div>
  );
}

export function HitRow({ h }: { h: Hit }) {
  const { t } = useTranslation();
  return (
    <li className="hit">
      <div>
        <TypeBadge type={h.type} /> <EntityLink e={h} />
        {h.status === "candidate" ? <span className="pill">{t("status.candidate")}</span> : null}
        {h.origin === "user" ? <span className="pill user">{t("origin.user")}</span> : null}
      </div>
      <Sources sources={h.sources} />
    </li>
  );
}

export function Loading({ error, loading }: { error: unknown; loading: boolean }) {
  const { t } = useTranslation();
  if (error) {
    const status = (error as { status?: number }).status;
    const msg = status === 401 ? t("common.unauthorized") : status === 404 ? t("common.not_found") : t("common.error");
    return <div className="error">{msg}</div>;
  }
  return loading ? <div className="muted">{t("common.loading")}</div> : null;
}
