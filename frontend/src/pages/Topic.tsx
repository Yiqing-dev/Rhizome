// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type TopicItem } from "../api";
import { EntityLink, Loading, TypeBadge } from "../components/common";
import { useLoad } from "../hooks";
import { href, go } from "../router";

const COLUMNS = ["dataset", "method", "idea", "claim", "work"] as const;
const ROLES = ["about", "applicable_to", "proposes", "uses", "produces", "evaluates"];

export default function TopicPage({ id, query }: { id: number; query?: URLSearchParams }) {
  const { t } = useTranslation();
  const role = query?.get("role") ?? "";
  const setRole = (r: string) => go(`topic/${id}`, { role: r || undefined });
  const page = useLoad(() => api.topicAssets(id, role || undefined), [id, role]);
  const [more, setMore] = useState<Record<string, TopicItem[]>>({});
  useEffect(() => setMore({}), [id, role]);
  const p = page.data;
  async function showMore(col: (typeof COLUMNS)[number]) {
    if (!p) return;
    const have = p.columns[col].length + (more[col]?.length ?? 0);
    const r = await api.topicAssets(id, role || undefined, col, have);
    setMore({ ...more, [col]: [...(more[col] ?? []), ...r.columns[col]] });
  }
  return (
    <div className={`stack ${page.loading && p ? "reloading" : ""}`}>
      <Loading error={page.error} loading={page.loading && !p} />
      {p && (
        <>
          <header className="entity-head">
            <div className="row wrap">
              <TypeBadge type="topic" />
              {p.topic.status === "candidate"
                ? <span className="pill">{t("status.candidate")}</span>
                : <span className="pill ok">{t("status.active")}</span>}
              {p.subtree_size > 1 ? <span className="muted small">{t("topic.subtree", { n: p.subtree_size - 1 })}</span> : null}
            </div>
            <h1>{p.topic.name}</h1>
            {p.topic.attrs.definition && <p className="definition">{p.topic.attrs.definition}</p>}
            {(p.parents.length > 0 || p.children.length > 0) && (
              <div className="breadcrumbs">
                {p.parents.map((x) => <a key={x.id} href={href(`topic/${x.id}`)}>↑ {x.name}</a>)}
                {p.children.map((x) => <a key={x.id} href={href(`topic/${x.id}`)}>↓ {x.name}</a>)}
              </div>
            )}
            <div className="row wrap">
              <div className="row wrap">
                <label className={`chip ${role === "" ? "on" : ""}`}>
                  <input type="radio" name="role" checked={role === ""} onChange={() => setRole("")} />{t("search.any_role")}
                </label>
                {ROLES.map((r) => (
                  <label key={r} className={`chip ${role === r ? "on" : ""}`}>
                    <input type="radio" name="role" checked={role === r} onChange={() => setRole(r)} />{t(`edge.${r}`)}
                  </label>
                ))}
              </div>
              {p.topic.status === "candidate" && (
                <button className="primary" onClick={() => api.decide("confirm_topic", { key: p.topic.key }).then(page.reload)}>
                  {t("topic.confirm")}
                </button>
              )}
            </div>
          </header>
          <div className="columns">
            {COLUMNS.map((col) => (
              <section key={col} className={`column t-${col}`}>
                <header><b>{t(`column.${col}`)}</b><span className="n">{p.totals?.[col] ?? p.columns[col].length}</span></header>
                {p.columns[col].length === 0 && <div className="empty-col empty" style={{ border: "none", background: "none", padding: "1rem" }}>{t("topic.no_items")}</div>}
                <ul>
                  {[...p.columns[col], ...(more[col] ?? [])].map((it: TopicItem) => (
                    <li key={it.id} className={it.contested ? "contested" : ""}>
                      <EntityLink e={it} />
                      {it.contested && <span className="pill warn">{t("topic.contested")}</span>}
                      {it.origin === "user" && <span className="pill user">{t("origin.user")}</span>}
                      <div className="roles">
                        {it.roles.map((r) => t(`edge.${r}`)).join(" · ")}
                        {it.year ? ` · ${it.year}` : ""}
                        {it.works > 1 ? ` · ${t("topic.n_works", { n: it.works })}` : ""}
                      </div>
                    </li>
                  ))}
                </ul>
                {(p.totals?.[col] ?? 0) > p.columns[col].length + (more[col]?.length ?? 0) && (
                  <button className="link" onClick={() => showMore(col)}>
                    {t("common.show_more", { n: (p.totals[col] - p.columns[col].length - (more[col]?.length ?? 0)) })}</button>
                )}
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
