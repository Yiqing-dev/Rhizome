// SPDX-License-Identifier: Apache-2.0
import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError, type Card, type EdgeView } from "../api";
import LocalGraph from "../components/LocalGraph";
import { EntityLink, Legend, Loading, TypeBadge, edgeToken } from "../components/common";
import { GraphIcon } from "../components/icons";
import { useLoad } from "../hooks";
import { fmtDate } from "../i18n";
import { href } from "../router";

const EDGE_ORDER = ["about", "applicable_to", "proposes", "uses", "produces", "evaluates", "supports", "contradicts",
  "extends", "relates_to", "cites", "is_a", "of_organism", "of_modality"];

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
      {status !== "rejected" && <button className="link danger" onClick={() => set("rejected")}>{t("action.reject")}</button>}
    </span>
  );
}

/** Corrections as human decisions (each one listed under Decisions, where it can be undone). */
function EditMenu({ card, onDone }: { card: Card; onDone: () => void }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [alias, setAlias] = useState("");
  const [into, setInto] = useState("");
  const [cands, setCands] = useState<{ key: string; name: string }[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  async function run(op: string, payload: Record<string, unknown>, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    try {
      const r = await api.decide(op, payload);
      setMsg({ ok: true, text: t(r.rebuild_job ? "edit.done_rebuild" : "edit.done", { id: r.id }) });
      setName(""); setAlias(""); setInto("");
      onDone();
    } catch (e) {
      setMsg({ ok: false, text: String((e as ApiError).detail ?? e) });
    }
  }
  async function suggest(q: string) {
    setInto(q);
    if (q.trim().length < 2) return setCands([]);
    const r = await api.search(q, { types: card.type, limit: 8 }).catch(() => ({ results: [] as any[] }));
    setCands(r.results.filter((h: any) => h.key !== card.key).map((h: any) => ({ key: h.key, name: h.name })));
  }
  return (
    <details className="edit-menu">
      <summary>{t("edit.title")}</summary>
      <div className="row wrap">
        <input className="grow" value={name} onChange={(e) => setName(e.target.value)} placeholder={t("edit.rename_placeholder")} />
        <button disabled={!name.trim()} onClick={() => run("rename", { key: card.key, name: name.trim() })}>{t("edit.rename")}</button>
      </div>
      <div className="row wrap">
        <input className="grow" value={alias} onChange={(e) => setAlias(e.target.value)} placeholder={t("edit.alias_placeholder")} />
        <button disabled={!alias.trim()} onClick={() => run("add_alias", { key: card.key, alias: alias.trim() })}>{t("edit.alias")}</button>
      </div>
      <div className="row wrap">
        <input className="grow" list={`merge-${card.id}`} value={into} onChange={(e) => suggest(e.target.value)} placeholder={t("edit.merge_placeholder")} />
        <datalist id={`merge-${card.id}`}>{cands.map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}</datalist>
        <button disabled={!into.trim()} onClick={() => run("merge", { from: card.key, into: into.trim() }, t("edit.merge_confirm", { into: into.trim() }))}>{t("edit.merge")}</button>
      </div>
      <div className="row wrap">
        {card.type === "work"
          ? <button className="danger" onClick={() => run("retract", { work: card.key }, t("edit.retract_confirm"))}>{t("edit.retract")}</button>
          : card.status !== "rejected" && <button className="danger" onClick={() => run("reject_entity", { key: card.key }, t("edit.reject_confirm"))}>{t("edit.reject")}</button>}
        <a className="small" href={href("decisions")}>{t("decisions.title")} →</a>
      </div>
      {msg && <div className={`notice ${msg.ok ? "ok" : "error"}`}>{msg.text}</div>}
    </details>
  );
}

function EdgeGroups({ card }: { card: Card }) {
  const { t } = useTranslation();
  const [extra, setExtra] = useState<Record<string, EdgeView[]>>({});
  const groups = Object.entries(card.edges).sort(([a], [b]) => EDGE_ORDER.indexOf(a) - EDGE_ORDER.indexOf(b))
    .map(([type, edges]) => [type, [...edges, ...(extra[type] ?? [])]] as [string, EdgeView[]]);
  async function more(type: string, have: number) {
    const r = await api.entityEdges(card.id, type, have);
    setExtra({ ...extra, [type]: [...(extra[type] ?? []), ...r.edges.filter((e) => !card.edges[type].some((x) => x.id === e.id))] });
  }
  return (
    <div className="edge-groups">
      {groups.map(([type, edges]) => (
        <section key={type} className="edge-group" style={{ "--ec": edgeToken(type) } as React.CSSProperties}>
          <header><i className="dot" /><b>{t(`edge.${type}`)}</b><span className="n">{card.edge_counts?.[type] ?? edges.length}</span></header>
          <ul>
            {edges.map((e: EdgeView) => (
              <li key={e.id} className={e.status === "rejected" ? "rejected" : ""}>
                <span className="arrow">{e.direction === "out" ? "→" : "←"}</span>
                <span>
                  <TypeBadge type={e.other.type} /> <EntityLink e={e.other} />
                  {e.evidence ? <> · <span className="evidence">{e.evidence}</span></> : null}
                  {e.attrs?.strength ? <span className={`pill s-${e.attrs.strength}`}>{t(`strength.${e.attrs.strength}`)}</span> : null}
                  {e.attrs?.origin === "user" ? <span className="pill user">{t("origin.user")}</span> : null}
                  {e.status !== "auto" ? <span className="pill">{t(`status.${e.status}`)}</span> : null}
                </span>
                <EdgeActions card={card} e={e} />
              </li>
            ))}
          </ul>
          {(card.edge_counts?.[type] ?? 0) > edges.length && (
            <button className="link" onClick={() => more(type, edges.length)}>
              {t("common.show_more", { n: (card.edge_counts?.[type] ?? 0) - edges.length })}</button>
          )}
        </section>
      ))}
    </div>
  );
}

function PaperCard({ card }: { card: Card }) {
  const { t, i18n } = useTranslation();
  const [showRaw, setShowRaw] = useState<number | null>(null);
  return (
    <div className="stack">
      {card.attrs.suspect_ids?.length ? <div className="notice warn">{t("paper.suspect", { ids: card.attrs.suspect_ids.join(", ") })}</div> : null}
      {(card.exports ?? []).map((ex) => (
        <section className="card export" key={ex.extraction_id}>
          <div className="export-head">
            <span className={`pill ${ex.depth === "deep" ? "ok" : ""}`} style={{ marginLeft: 0 }}>{t(`depth.${ex.depth}`)}</span>
            <span className="muted small">{fmtDate(ex.created_at, i18n.language)}{ex.prompt_version ? ` · ${ex.prompt_version}` : ""}</span>
            {ex.has_pdf ? <span className="pill">PDF</span> : null}
          </div>
          <ol className="tldr">{ex.tldr.map((x, i) => <li key={i}>{x}</li>)}</ol>
          {ex.user_insights.length ? (<>
            <div className="section-label">{t("paper.your_view")}</div>
            <div className="stack-sm">{ex.user_insights.map((u, i) => <div key={i} className="user-insight">{u.text}</div>)}</div>
          </>) : null}
          {ex.claims.length ? (<>
            <div className="section-label">{t("paper.logic")}</div>
            <div className="claims">{ex.claims.map((c, i) => (
              <div key={i} className="claim">
                {c.text}
                <div className="tags">
                  <span className="evidence">{c.evidence}</span>
                  <span className="pill">{t(`evidence.${c.evidence_type}`)}</span>
                  {c.logic_jump ? <span className="pill warn">{t("paper.logic_jump")}</span> : null}
                  {c.boundary ? <span className="muted small">· {c.boundary}</span> : null}
                </div>
              </div>))}</div>
          </>) : null}
          {ex.issues.length ? (<>
            <div className="section-label">{t("paper.issues")}</div>
            <ul className="issues">{ex.issues.map((x, i) => (
              <li key={i}><span className={`pill ${x.severity === "minor" ? "" : "warn"}`} style={{ marginLeft: 0 }}>{t(`severity.${x.severity}`)}</span> {x.text}
                {x.test ? <span className="muted small"> — {x.test}</span> : null}</li>))}</ul>
          </>) : null}
          <div>
            <button className="link" style={{ paddingLeft: 0 }} onClick={() => setShowRaw(showRaw === ex.extraction_id ? null : ex.extraction_id)}>
              {showRaw === ex.extraction_id ? t("paper.raw_hide") : t("paper.raw")}
            </button>
          </div>
          {showRaw === ex.extraction_id && <pre className="raw">{ex.raw}</pre>}
        </section>
      ))}
    </div>
  );
}

function AssetAttrs({ card }: { card: Card }) {
  const { t } = useTranslation();
  const hidden = new Set(["tldr", "rxf_extraction", "transfer", "links", "links_unresolved", "verified", "weight", "origin", "edited", "original_text"]);
  const rows = Object.entries(card.attrs).filter(([k, v]) => !hidden.has(k) && v !== null && typeof v !== "object");
  if (!rows.length && !card.attrs.transfer && card.aliases.length <= 1) return null;
  return (
    <section className="card" style={{ padding: "0.9rem 1.1rem" }}>
      <dl className="attrs">
        {rows.map(([k, v]) => (<Fragment key={k}><dt>{t(`attr.${k}`, { defaultValue: k })}</dt><dd>{String(v)}</dd></Fragment>))}
        {card.attrs.transfer && (<><dt>{t("asset.transfer")}</dt>
          <dd>{t(`transfer.${card.attrs.transfer.type}`)}{card.attrs.transfer.to ? ` → ${card.attrs.transfer.to}` : ""}
            {card.attrs.transfer.barrier ? <span className="muted"> · {card.attrs.transfer.barrier}</span> : null}</dd></>)}
        {card.aliases.length > 1 && (<><dt>{t("asset.aliases")}</dt><dd>{card.aliases.map((a) => a.alias).join(" · ")}</dd></>)}
      </dl>
    </section>
  );
}

export default function EntityPage({ id }: { id: number }) {
  const { t } = useTranslation();
  const [hops, setHops] = useState(1);
  const card = useLoad(() => api.entity(id), [id]);
  const graph = useLoad(() => api.neighbors(id, hops), [id, hops]);
  const related = useLoad(() => api.related(id), [id]);
  const c = card.data;
  const nodeTypes = graph.data ? new Set(graph.data.nodes.map((n) => n.type)) : undefined;
  const edgeTypes = graph.data ? new Set(graph.data.edges.map((e) => e.type)) : undefined;
  return (
    <div className="stack">
      <Loading error={card.error} loading={card.loading && !c} />
      {c && (
        <>
          <header className="entity-head">
            <div className="row wrap">
              <TypeBadge type={c.type} />
              {c.status === "candidate" ? <span className="pill">{t("status.candidate")}</span> : null}
              {c.status === "rejected" ? <span className="pill warn">{t("status.rejected")}</span> : null}
              {c.attrs.origin === "user" ? <span className="pill user">{t("origin.user")}</span> : null}
              {c.attrs.verified === "verified" ? <span className="pill ok">{t("asset.verified")}</span> : null}
            </div>
            <h1>{c.name}</h1>
            <div className="idline">
              {c.work?.year ? <span>{c.work.year}</span> : null}
              {c.work?.dois?.map((d) => <span key={d}>{t("paper.doi")} <code>{d}</code></span>)}
              {c.work ? <span>{t("paper.tier", { tier: c.work.tier })}</span> : null}
              {c.external_id && c.type !== "work" ? <span>{t("asset.external_id")} <code>{c.external_id}</code></span> : null}
              {c.attrs.venue ? <span>{c.attrs.venue}</span> : null}
            </div>
          </header>
          {c.type === "work" ? <PaperCard card={c} /> : <AssetAttrs card={c} />}
          <EditMenu card={c} onDone={card.reload} />
          <div className="two-col">
            <EdgeGroups card={c} />
            <div className="stack">
              <section className="graph-card">
                <header>
                  <GraphIcon />
                  <b>{t("graph.local")}</b>
                  <span className="spacer grow" />
                  <select value={hops} onChange={(e) => setHops(Number(e.target.value))}>
                    <option value={1}>{t("graph.hops", { n: 1 })}</option>
                    <option value={2}>{t("graph.hops", { n: 2 })}</option>
                  </select>
                </header>
                {graph.data && <LocalGraph graph={graph.data} center={id} />}
                {graph.data && <Legend nodeTypes={nodeTypes} edgeTypes={edgeTypes} />}
                {graph.data?.has_more && <p className="muted small" style={{ padding: "0.4rem 0.9rem" }}>{t("graph.truncated", { n: graph.data.total_nodes })}</p>}
              </section>
              <section className="stack-sm">
                <h3>{t("entity.related")}</h3>
                {related.data && related.data.related.length === 0 && <p className="muted small">{t("entity.no_related")}</p>}
                <ul className="related">
                  {(related.data?.related ?? []).map((r: any) => (
                    <li key={r.work_id ?? r.id}>
                      <a href={href(`entity/${r.work_id ?? r.id}`)}>{r.title ?? r.name}</a>
                      {r.via ? (
                        <div className="via">
                          {Array.from(new Map<string, any>(r.via.map((v: any) => [v.dimension + v.theirs, v])).values()).slice(0, 3).map((v: any, i: number) => (
                            <span key={i}><TypeBadge type={v.dimension} /> {v.theirs}</span>
                          ))}
                        </div>
                      ) : r.type ? <div className="via"><TypeBadge type={r.type} /></div> : null}
                    </li>
                  ))}
                </ul>
              </section>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
