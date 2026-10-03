// SPDX-License-Identifier: Apache-2.0
import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type Card, type EdgeView } from "../api";
import LocalGraph from "../components/LocalGraph";
import { EntityLink, Loading, TypeBadge } from "../components/common";
import { useLoad } from "../hooks";
import { fmtDate } from "../i18n";
import { href } from "../router";

function EdgeGroups({ card }: { card: Card }) {
  const { t } = useTranslation();
  const groups = Object.entries(card.edges).sort(([a], [b]) => a.localeCompare(b));
  return (
    <div className="edge-groups">
      {groups.map(([type, edges]) => (
        <section key={type}>
          <h3>{t(`edge.${type}`)} <span className="muted">({edges.length})</span></h3>
          <ul>
            {edges.map((e: EdgeView) => (
              <li key={e.id} className={e.status === "rejected" ? "rejected" : ""}>
                <span className="muted">{e.direction === "out" ? "→" : "←"}</span> <TypeBadge type={e.other.type} />{" "}
                <EntityLink e={e.other} />
                {e.evidence ? <span className="evidence"> · {e.evidence}</span> : null}
                {e.attrs?.strength ? <span className={`pill s-${e.attrs.strength}`}>{t(`strength.${e.attrs.strength}`)}</span> : null}
                {e.status !== "auto" ? <span className="pill">{t(`status.${e.status}`)}</span> : null}
                <EdgeActions card={card} e={e} />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function EdgeActions({ card, e }: { card: Card; e: EdgeView }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState(e.status);
  const src = e.direction === "out" ? card.key : e.other.key;
  const dst = e.direction === "out" ? e.other.key : card.key;
  const set = async (s: string) => {
    await api.decide("edge_status", { src, dst, type: e.type, status: s });
    setStatus(s);
  };
  return (
    <span className="edge-actions">
      {status !== "confirmed" && <button className="link" onClick={() => set("confirmed")}>{t("action.confirm")}</button>}
      {status !== "rejected" && <button className="link" onClick={() => set("rejected")}>{t("action.reject")}</button>}
    </span>
  );
}

function PaperCard({ card }: { card: Card }) {
  const { t, i18n } = useTranslation();
  const [showRaw, setShowRaw] = useState<number | null>(null);
  return (
    <div className="stack">
      {card.work && (
        <div className="muted">
          {card.work.year}{card.work.dois.length ? ` · ${card.work.dois.join(", ")}` : ""} · {t("paper.tier", { tier: card.work.tier })}
        </div>
      )}
      {card.attrs.suspect_ids?.length ? <div className="warn">{t("paper.suspect", { ids: card.attrs.suspect_ids.join(", ") })}</div> : null}
      {(card.exports ?? []).map((ex) => (
        <section className="panel" key={ex.extraction_id}>
          <h3>{t(`depth.${ex.depth}`)} <span className="muted">· {fmtDate(ex.created_at, i18n.language)} · {ex.prompt_version}</span></h3>
          <ol className="tldr">{ex.tldr.map((x, i) => <li key={i}>{x}</li>)}</ol>
          {ex.user_insights.length ? (<>
            <h4>{t("paper.your_view")}</h4>
            <ul>{ex.user_insights.map((u, i) => <li key={i} className="user-insight">{u.text}</li>)}</ul>
          </>) : null}
          {ex.claims.length ? (<>
            <h4>{t("paper.logic")}</h4>
            <ul>{ex.claims.map((c, i) => (
              <li key={i}>{c.text} <span className="evidence">· {c.evidence}</span>
                <span className="pill">{t(`evidence.${c.evidence_type}`)}</span>
                {c.logic_jump ? <span className="pill warn">{t("paper.logic_jump")}</span> : null}
              </li>))}</ul>
          </>) : null}
          {ex.issues.length ? (<>
            <h4>{t("paper.issues")}</h4>
            <ul>{ex.issues.map((x, i) => <li key={i}><span className="pill">{t(`severity.${x.severity}`)}</span> {x.text}</li>)}</ul>
          </>) : null}
          <button className="link" onClick={() => setShowRaw(showRaw === ex.extraction_id ? null : ex.extraction_id)}>
            {t("paper.raw")}
          </button>
          {showRaw === ex.extraction_id && <pre className="raw">{ex.raw}</pre>}
        </section>
      ))}
    </div>
  );
}

function AssetAttrs({ card }: { card: Card }) {
  const { t } = useTranslation();
  const hidden = new Set(["tldr", "rxf_extraction", "transfer", "links", "links_unresolved"]);
  const rows = Object.entries(card.attrs).filter(([k, v]) => !hidden.has(k) && v !== null && typeof v !== "object");
  return (
    <dl className="attrs">
      {card.external_id && (<><dt>{t("asset.external_id")}</dt><dd>{card.external_id}</dd></>)}
      {rows.map(([k, v]) => (<Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>))}
      {card.attrs.transfer && (<><dt>{t("asset.transfer")}</dt>
        <dd>{t(`transfer.${card.attrs.transfer.type}`)} → {card.attrs.transfer.to} · {card.attrs.transfer.barrier}</dd></>)}
      {card.aliases.length > 1 && (<><dt>{t("asset.aliases")}</dt><dd>{card.aliases.map((a) => a.alias).join(" · ")}</dd></>)}
    </dl>
  );
}

export default function EntityPage({ id }: { id: number }) {
  const { t } = useTranslation();
  const [hops, setHops] = useState(1);
  const card = useLoad(() => api.entity(id), [id]);
  const graph = useLoad(() => api.neighbors(id, hops), [id, hops]);
  const related = useLoad(() => api.related(id), [id]);
  const c = card.data;
  return (
    <div className="stack">
      <Loading error={card.error} loading={card.loading && !c} />
      {c && (
        <>
          <h1><TypeBadge type={c.type} /> {c.name}</h1>
          {c.type === "work" ? <PaperCard card={c} /> : <AssetAttrs card={c} />}
          <div className="two-col">
            <EdgeGroups card={c} />
            <section>
              <div className="row">
                <h3>{t("graph.local")}</h3>
                <select value={hops} onChange={(e) => setHops(Number(e.target.value))}>
                  <option value={1}>{t("graph.hops", { n: 1 })}</option>
                  <option value={2}>{t("graph.hops", { n: 2 })}</option>
                </select>
              </div>
              {graph.data && <LocalGraph graph={graph.data} center={id} />}
              {graph.data?.has_more && <p className="muted">{t("graph.truncated", { n: graph.data.total_nodes })}</p>}
              <h3>{t("entity.related")}</h3>
              <ul>
                {(related.data?.related ?? []).map((r: any) => (
                  <li key={r.work_id ?? r.id}>
                    <a href={href(`entity/${r.work_id ?? r.id}`)}>{r.title ?? r.name}</a>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
