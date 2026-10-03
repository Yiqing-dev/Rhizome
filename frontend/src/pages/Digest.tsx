// SPDX-License-Identifier: Apache-2.0
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { EntityLink, Loading, TypeBadge } from "../components/common";
import { useLoad } from "../hooks";

/** Weekly digest: two asset cards side by side; explanations are written by Claude via MCP. */
export default function DigestPage() {
  const { t } = useTranslation();
  const d = useLoad(() => api.digest(), []);
  async function mark(id: number, useful: boolean) {
    await api.resolve(id, useful ? "useful" : "useless");
    d.reload();
  }
  return (
    <div className="stack">
      <h1>{t("digest.title")}</h1>
      <p className="muted">{t("digest.hint")}</p>
      <div className="row">
        <button onClick={() => api.runJob("nightly").then(() => setTimeout(d.reload, 1500))}>{t("digest.run_now")}</button>
      </div>
      <Loading error={d.error} loading={d.loading && !d.data} />
      {d.data && (
        <>
          <h2>{t("digest.pairs")}</h2>
          {!d.data.pairs.length && <p className="muted">{t("digest.no_pairs")}</p>}
          {d.data.pairs.map((p: any) => (
            <div className="pair panel" key={p.item_id}>
              <div className="side"><TypeBadge type={p.a.type} /> <EntityLink e={p.a} /></div>
              <div className="muted">⇄ {p.score.toFixed(2)}</div>
              <div className="side"><TypeBadge type={p.b.type} /> <EntityLink e={p.b} /></div>
              <div className="row">
                <button onClick={() => mark(p.item_id, true)}>{t("review.action.useful")}</button>
                <button onClick={() => mark(p.item_id, false)}>{t("review.action.useless")}</button>
              </div>
            </div>
          ))}
          <h2>{t("digest.contested")}</h2>
          {!d.data.contradictions.length && !d.data.contradictions_pending_review.length && <p className="muted">{t("digest.no_contested")}</p>}
          <ul>
            {d.data.contradictions.map((c: any, i: number) => (
              <li key={i}><EntityLink e={c.work} /> ⟂ <EntityLink e={c.claim} /> <span className="evidence">· {c.evidence}</span></li>
            ))}
            {d.data.contradictions_pending_review.map((c: any) => (
              <li key={c.item_id}><span className="pill">{t("status.pending")}</span> {c.new_text} ⟂ {c.claim_text}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
