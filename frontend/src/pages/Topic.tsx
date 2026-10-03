// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type TopicItem } from "../api";
import { EntityLink, Loading } from "../components/common";
import { useLoad } from "../hooks";
import { href } from "../router";

const COLUMNS = ["dataset", "method", "idea", "claim", "work"] as const;

export default function TopicPage({ id }: { id: number }) {
  const { t } = useTranslation();
  const [role, setRole] = useState("");
  const page = useLoad(() => api.topicAssets(id, role || undefined), [id, role]);
  const p = page.data;
  return (
    <div className="stack">
      <Loading error={page.error} loading={page.loading && !p} />
      {p && (
        <>
          <h1>{p.topic.name} {p.topic.status === "candidate" && <span className="pill">{t("status.candidate")}</span>}</h1>
          {p.topic.attrs.definition && <p>{p.topic.attrs.definition}</p>}
          <div className="row wrap muted">
            {p.parents.map((x) => <a key={x.id} href={href(`topic/${x.id}`)}>↑ {x.name}</a>)}
            {p.children.map((x) => <a key={x.id} href={href(`topic/${x.id}`)}>↓ {x.name}</a>)}
          </div>
          <div className="row">
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="">{t("search.any_role")}</option>
              {["about", "applicable_to", "proposes", "uses", "produces", "evaluates"].map((r) =>
                <option key={r} value={r}>{t(`edge.${r}`)}</option>)}
            </select>
            {p.topic.status === "candidate" && (
              <button onClick={() => api.decide("confirm_topic", { key: p.topic.key }).then(page.reload)}>
                {t("topic.confirm")}
              </button>
            )}
          </div>
          <div className="columns">
            {COLUMNS.map((col) => (
              <section key={col} className="column">
                <h3>{t(`column.${col}`)} <span className="muted">({p.columns[col].length})</span></h3>
                <ul>
                  {p.columns[col].map((it: TopicItem) => (
                    <li key={it.id} className={it.contested ? "contested" : ""}>
                      <EntityLink e={it} />
                      {it.contested && <span className="pill warn">{t("topic.contested")}</span>}
                      <div className="muted small">
                        {it.roles.map((r) => t(`edge.${r}`)).join(" · ")}
                        {it.works > 1 ? ` · ${t("topic.n_works", { n: it.works })}` : ""}
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
