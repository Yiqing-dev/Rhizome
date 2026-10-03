// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Loading } from "../components/common";
import { useLoad } from "../hooks";
import { backendLang, setLanguage } from "../i18n";

export default function SettingsPage() {
  const { t, i18n } = useTranslation();
  const st = useLoad(() => api.settings(), []);
  const [saved, setSaved] = useState(false);
  const [remotes, setRemotes] = useState<string | null>(null);

  async function patch(b: Record<string, unknown>) {
    await api.patchSettings(b);
    setSaved(true);
    st.reload();
  }

  async function lang(l: "en" | "zh-CN") {
    setLanguage(l);
    await patch({ language: backendLang(l) });
  }

  async function downloadVocab() {
    const text = await api.vocab();
    const url = URL.createObjectURL(new Blob([text], { type: "text/yaml" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "rhizome-vocab.yaml";
    a.click();
    URL.revokeObjectURL(url);
  }

  const s = st.data;
  return (
    <div className="stack narrow">
      <h1>{t("settings.title")}</h1>
      <Loading error={st.error} loading={st.loading && !s} />
      {saved && <div className="ok">{t("settings.saved")}</div>}
      <section className="panel">
        <h2>{t("settings.language")}</h2>
        <div className="row">
          <button className={i18n.language === "en" ? "active" : ""} onClick={() => lang("en")}>{t("settings.lang_en")}</button>
          <button className={i18n.language === "zh-CN" ? "active" : ""} onClick={() => lang("zh-CN")}>{t("settings.lang_zh")}</button>
        </div>
        <p className="muted small">{t("settings.language_hint")}</p>
      </section>
      {s && (
        <>
          <section className="panel">
            <h2>{t("settings.paths")}</h2>
            <dl className="attrs">
              <dt>{t("settings.data_dir")}</dt><dd>{s.data_dir}</dd>
              <dt>{t("settings.inbox")}</dt><dd>{s.inbox_dir ?? `${s.data_dir}/inbox`}</dd>
            </dl>
            <label className="check"><input type="checkbox" checked={s.offline} onChange={(e) => patch({ offline: e.target.checked })} />
              {t("settings.offline")}</label>
          </section>
          <section className="panel">
            <h2>{t("settings.models")}</h2>
            <p className="muted small">{t("settings.models_hint")}</p>
            <label>{t("settings.embedder")}
              <select value={s.embedder} onChange={(e) => patch({ embedder: e.target.value })}>
                <option value="hashing">{t("settings.builtin")}</option><option value="bge-m3">bge-m3</option>
              </select></label>
            <label>{t("settings.reranker")}
              <select value={s.reranker} onChange={(e) => patch({ reranker: e.target.value })}>
                <option value="lexical">{t("settings.builtin")}</option><option value="bge-reranker-v2-m3">bge-reranker-v2-m3</option>
              </select></label>
            <label>{t("settings.nli")}
              <select value={s.nli} onChange={(e) => patch({ nli: e.target.value })}>
                <option value="none">{t("settings.off")}</option><option value="mdeberta">mDeBERTa-v3-base-xnli</option>
              </select></label>
            <label>{t("settings.inference")}
              <select value={s.inference_backend} onChange={(e) => patch({ inference_backend: e.target.value })}>
                <option value="queue">{t("settings.inference_queue")}</option>
                <option value="local">{t("settings.inference_local")}</option>
              </select></label>
            <p className="muted small">{t("settings.rebuild_hint")}</p>
            <button onClick={() => api.runJob("rebuild")}>{t("settings.rebuild")}</button>
          </section>
          <section className="panel">
            <h2>{t("settings.review")}</h2>
            <label>{t("settings.daily_new")}
              <input type="number" defaultValue={s.review_daily_new} onBlur={(e) => patch({ review_daily_new: Number(e.target.value) })} /></label>
            <label>{t("settings.daily_max")}
              <input type="number" defaultValue={s.review_daily_max} onBlur={(e) => patch({ review_daily_max: Number(e.target.value) })} /></label>
          </section>
          <section className="panel">
            <h2>{t("settings.remotes")}</h2>
            <p className="muted small">{t("settings.remotes_hint")}</p>
            <textarea rows={6} className="mono" value={remotes ?? JSON.stringify(s.remotes, null, 2)} onChange={(e) => setRemotes(e.target.value)} />
            <button onClick={() => remotes && patch({ remotes: JSON.parse(remotes) })}>{t("settings.save")}</button>
          </section>
          <section className="panel">
            <h2>{t("settings.vocab")}</h2>
            <p className="muted small">{t("settings.vocab_hint")}</p>
            <button onClick={downloadVocab}>{t("settings.vocab_download")}</button>
          </section>
        </>
      )}
    </div>
  );
}
