// SPDX-License-Identifier: Apache-2.0
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { EmptyState, EntityLink, Loading, TypeBadge } from "../components/common";
import { LinkIcon } from "../components/icons";
import { useJob, useLoad } from "../hooks";
import { fmtDate } from "../i18n";

/** Weekly digest: two asset cards side by side; explanations are written by Claude via MCP. */
export default function DigestPage() {
  const { t } = useTranslation();
  const d = useLoad(() => api.digest(), []);
  const nightly = useJob("nightly", () => d.reload(), { force_synthesis: true });
  const { i18n } = useTranslation();
  async function mark(id: number, useful: boolean) {
    await api.resolve(id, useful ? "useful" : "useless");
    d.reload();
  }
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>{t("digest.title")}</h1>
          <p className="sub">{t("digest.hint")}</p>
        </div>
        <button onClick={nightly.start} disabled={nightly.running}>{nightly.running ? t("jobs.running") : t("digest.run_now")}</button>
      </div>
      {nightly.job?.status === "failed" && <div className="notice error"><pre>{nightly.job.error}</pre></div>}
      {d.data && (
        <p className="hint row wrap">
          <span>{t("digest.last_batch")}: {d.data.last_synthesis ? fmtDate(d.data.last_synthesis, i18n.language) : t("digest.never")}</span>
          <span>· {t("digest.threshold")}: <span className="mono">{d.data.threshold.toFixed(2)}</span>
            {Math.abs(d.data.threshold - d.data.threshold_default) > 0.001 && (
              <> ({t("digest.threshold_default", { v: d.data.threshold_default.toFixed(2) })}){" "}
                <button className="link" onClick={() => api.resetSynthesisThreshold().then(d.reload)}>{t("digest.threshold_reset")}</button></>
            )}</span>
        </p>
      )}
      <Loading error={d.error} loading={d.loading && !d.data} />
      {d.data && (
        <>
          <section className="stack-sm">
            <h3>{t("digest.pairs")}</h3>
            {!d.data.pairs.length && <EmptyState icon={<LinkIcon />} title={t("digest.no_pairs")} hint={t("digest.no_pairs_hint")} />}
            {d.data.pairs.map((p: any) => (
              <div className="card pair" key={p.item_id}>
                <div className="side"><div className="lbl"><TypeBadge type={p.a.type} /></div><div className="name"><EntityLink e={p.a} /></div></div>
                <div className="link-mark"><b>⇄</b><span>{t("digest.similarity")}</span><span className="mono">{p.score.toFixed(2)}</span></div>
                <div className="side"><div className="lbl"><TypeBadge type={p.b.type} /></div><div className="name"><EntityLink e={p.b} /></div></div>
                <div className="pair-actions">
                  <button className="primary" onClick={() => mark(p.item_id, true)}>{t("review.action.useful")}</button>
                  <button onClick={() => mark(p.item_id, false)}>{t("review.action.useless")}</button>
                </div>
              </div>
            ))}
          </section>
          <section className="stack-sm">
            <h3>{t("digest.contested")}</h3>
            {!d.data.contradictions.length && !d.data.contradictions_pending_review.length && <p className="muted small">{t("digest.no_contested")}</p>}
            <ul className="plain related">
              {d.data.contradictions.map((c: any, i: number) => (
                <li key={i}><EntityLink e={c.work} /> <span className="error">⟂</span> <EntityLink e={c.claim} />
                  {c.evidence ? <> · <span className="evidence">{c.evidence}</span></> : null}</li>
              ))}
              {d.data.contradictions_pending_review.map((c: any) => (
                <li key={c.item_id}><span className="pill warn" style={{ marginLeft: 0 }}>{t("status.pending")}</span> {c.new_text} <span className="error">⟂</span> {c.claim_text}</li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
