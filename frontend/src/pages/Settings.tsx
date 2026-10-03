// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Loading } from "../components/common";
import { SettingsIcon } from "../components/icons";
import { useLoad } from "../hooks";
import { backendLang, setLanguage } from "../i18n";

export default function SettingsPage() {
  const { t, i18n } = useTranslation();
  const st = useLoad(() => api.settings(), []);
  const [saved, setSaved] = useState(false);
  const [remotes, setRemotes] = useState<string | null>(null);
  const [remotesErr, setRemotesErr] = useState(false);

  async function patch(b: Record<string, unknown>) {
    await api.patchSettings(b);
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
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

  function saveRemotes() {
    if (remotes === null) return;
    try {
      const parsed = JSON.parse(remotes);
      setRemotesErr(false);
      patch({ remotes: parsed });
    } catch {
      setRemotesErr(true);
    }
  }

  const s = st.data;
  const cur = i18n.language.startsWith("zh") ? "zh-CN" : "en";
  return (
    <div className="stack narrow">
      <div className="page-head">
        <h1>{t("settings.title")}</h1>
        {saved && <span className="pill ok">{t("settings.saved")}</span>}
      </div>
      <Loading error={st.error} loading={st.loading && !s} />
      <section className="panel">
        <h2>{t("settings.language")}</h2>
        <div className="row">
          <button className={cur === "en" ? "active" : ""} onClick={() => lang("en")} lang="en">{t("settings.lang_en")}</button>
          <button className={cur === "zh-CN" ? "active" : ""} onClick={() => lang("zh-CN")} lang="zh-CN">{t("settings.lang_zh")}</button>
        </div>
        <p className="hint">{t("settings.language_hint")}</p>
      </section>
      {s && (
        <>
          <section className="panel">
            <h2>{t("settings.paths")}</h2>
            <dl className="attrs">
              <dt>{t("settings.data_dir")}</dt><dd><code className="mono">{s.data_dir}</code></dd>
              <dt>{t("settings.inbox")}</dt><dd><code className="mono">{s.inbox_dir ?? `${s.data_dir}/inbox`}</code></dd>
            </dl>
            <label className="check"><input type="checkbox" checked={s.offline} onChange={(e) => patch({ offline: e.target.checked })} />
              {t("settings.offline")}</label>
          </section>
          <section className="panel">
            <h2><SettingsIcon />{t("settings.models")}</h2>
            <p className="hint">{t("settings.models_hint")}</p>
            <div className="form-grid">
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
            </div>
            <div className="row wrap">
              <button onClick={() => api.runJob("rebuild")}>{t("settings.rebuild")}</button>
              <span className="hint">{t("settings.rebuild_hint")}</span>
            </div>
          </section>
          <section className="panel">
            <h2>{t("settings.review")}</h2>
            <div className="form-grid">
              <label>{t("settings.daily_new")}
                <input type="number" min={0} defaultValue={s.review_daily_new} onBlur={(e) => patch({ review_daily_new: Number(e.target.value) })} /></label>
              <label>{t("settings.daily_max")}
                <input type="number" min={0} defaultValue={s.review_daily_max} onBlur={(e) => patch({ review_daily_max: Number(e.target.value) })} /></label>
            </div>
          </section>
          <section className="panel">
            <h2>{t("settings.remotes")}</h2>
            <p className="hint">{t("settings.remotes_hint")}</p>
            <textarea rows={6} className="mono" value={remotes ?? JSON.stringify(s.remotes, null, 2)} onChange={(e) => setRemotes(e.target.value)} />
            {remotesErr && <div className="notice error">{t("settings.remotes_invalid")}</div>}
            <div className="row"><button onClick={saveRemotes} disabled={remotes === null}>{t("settings.save")}</button></div>
          </section>
          <section className="panel">
            <h2>{t("settings.vocab")}</h2>
            <p className="hint">{t("settings.vocab_hint")}</p>
            <div className="row"><button onClick={downloadVocab}>{t("settings.vocab_download")}</button></div>
          </section>
        </>
      )}
    </div>
  );
}
