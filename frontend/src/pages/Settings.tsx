// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api";
import { Loading } from "../components/common";
import { SettingsIcon } from "../components/icons";
import { useJob, useLoad } from "../hooks";
import { backendLang, fmtDate, setLanguage } from "../i18n";

export default function SettingsPage() {
  const { t, i18n } = useTranslation();
  const st = useLoad(() => api.settings(), []);
  const sys = useLoad(() => api.system(), []);
  const [newDir, setNewDir] = useState("");
  const [dirMsg, setDirMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [claudeMsg, setClaudeMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [saved, setSaved] = useState(false);
  const [remotes, setRemotes] = useState<string | null>(null);
  const [remotesErr, setRemotesErr] = useState(false);
  const [backupDirEdit, setBackupDir] = useState<string | null>(null);
  const [backupMsg, setBackupMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const rebuild = useJob("rebuild");
  const [apiKey, setApiKey] = useState("");
  const [keyMsg, setKeyMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const keySource = sys.data?.secrets?.anthropic_api_key ?? null;
  async function saveKey(value: string | null) {
    try {
      await api.setSecret("anthropic_api_key", value);
      setApiKey("");
      setKeyMsg({ ok: true, text: t(value ? "settings.anthropic_key_saved" : "settings.anthropic_key_removed") });
      sys.reload();
    } catch (e) {
      setKeyMsg({ ok: false, text: String((e as ApiError).detail ?? e) });
    }
  }
  const ADV_KEY = "rhizome.settings.advanced";
  const [adv, setAdv] = useState(() => { try { return localStorage.getItem(ADV_KEY) === "1"; } catch { return false; } });
  const rememberAdv = (open: boolean) => { setAdv(open); try { localStorage.setItem(ADV_KEY, open ? "1" : "0"); } catch { /* ignore */ } };

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

  function save(text: string, name: string, type: string) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }

  const [vocabCandidates, setVocabCandidates] = useState(true);
  async function downloadVocab() {
    save(await api.vocab(vocabCandidates, true), "rhizome-vocab.yaml", "text/yaml");
  }

  const instrLang = () => (i18n.language.startsWith("zh") ? "zh_CN" : "en");

  async function downloadInstructions() {
    save(await api.rxfInstructions(instrLang()), `rhizome-export-instructions.${instrLang()}.md`, "text/markdown");
  }

  async function copyInstructions() {
    const text = await api.rxfInstructions(instrLang());
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      save(text, `rhizome-export-instructions.${instrLang()}.md`, "text/markdown");  // clipboard blocked
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
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

  async function moveDir(path: string | null) {
    try {
      const r = await api.moveDataDir(path);
      const claude = r.claude_config_updated?.length ? "\n\n" + t("settings.dir_claude_updated") : "";
      setDirMsg({ ok: true, text: (r.restart_required ? t("settings.dir_moved", { dir: r.data_dir }) : t("settings.dir_same")) + claude });
    } catch (e) {
      setDirMsg({ ok: false, text: String((e as ApiError).detail ?? e) });
    }
  }

  async function patchChecked(b: Record<string, unknown>) {
    try {
      await patch(b);
      sys.reload();
      setBackupMsg(null);
    } catch (e) {
      setBackupMsg({ ok: false, text: String((e as ApiError).detail ?? e) });
    }
  }

  async function backupNow() {
    try {
      const r = await api.backupNow();
      setBackupMsg({ ok: true, text: t("settings.backup_done", { path: r.path ?? "" }) });
      sys.reload();
    } catch (e) {
      setBackupMsg({ ok: false, text: String((e as ApiError).detail ?? e) });
    }
  }

  async function connectClaude() {
    try {
      const r = await api.connectClaude();
      setClaudeMsg({ ok: true, text: t("settings.claude_done", { files: r.written.join("\n") }) });
    } catch (e) {
      setClaudeMsg({ ok: false, text: String((e as ApiError).detail ?? e) });
    }
  }

  const s = st.data;
  const si = sys.data;
  const backupDir = backupDirEdit ?? (s?.backup_dir ?? "");
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
          <section className="panel" id="project-setup">
            <h2>{t("settings.project")}</h2>
            <p className="hint">{t("settings.project_hint")}</p>
            <div className="row wrap">
              <button className="primary" onClick={copyInstructions}>{copied ? t("settings.copied") : t("settings.project_copy")}</button>
              <button onClick={downloadInstructions}>{t("settings.project_download")}</button>
              <button onClick={downloadVocab}>{t("settings.vocab_download")}</button>
              <label className="small"><input type="checkbox" checked={vocabCandidates} onChange={(e) => setVocabCandidates(e.target.checked)} /> {t("settings.vocab_candidates")}</label>
            </div>
            <p className="hint">{t("settings.vocab_hint")}</p>
          </section>
          <section className="panel">
            <h2>{t("settings.paths")}</h2>
            <dl className="attrs">
              <dt>{t("settings.data_dir")}</dt>
              <dd className="row wrap"><code className="mono">{si?.data_dir ?? s.data_dir}</code>
                <button className="link" onClick={() => api.openFolder("data")}>{t("settings.open")}</button></dd>
              <dt>{t("settings.inbox")}</dt>
              <dd className="row wrap"><code className="mono">{si?.inbox ?? s.inbox_dir}</code>
                <button className="link" onClick={() => api.openFolder("inbox")}>{t("settings.open")}</button></dd>
              <dt>{t("settings.logs")}</dt>
              <dd className="row wrap"><code className="mono">{si?.logs}</code>
                <button className="link" onClick={() => api.openFolder("logs")}>{t("settings.open")}</button></dd>
              {si?.app_dir && (<><dt>{t("settings.app_dir")}</dt><dd><code className="mono">{si.app_dir}</code></dd></>)}
            </dl>
          </section>
          <section className="panel">
            <h2>{t("settings.backups")}</h2>
            <p className="hint">{t("settings.backups_hint")}</p>
            {si && (
              <dl className="attrs">
                <dt>{t("settings.backups_dir")}</dt>
                <dd className="row wrap"><code className="mono">{si.backups.dir}</code>
                  <button className="link" onClick={() => api.openFolder("backups")}>{t("settings.open")}</button></dd>
                <dt>{t("settings.backups_last")}</dt>
                <dd>{si.backups.last_daily ? fmtDate(si.backups.last_daily, i18n.language) : t("settings.backups_never")}
                  {" · "}{t("settings.backups_count", { n: si.backups.count, size: (si.backups.bytes / 1048576).toFixed(1) })}</dd>
              </dl>
            )}
            {si?.backups.error && <div className="notice error">{si.backups.error}</div>}
            <div className="row wrap">
              <button className="primary" onClick={backupNow}>{t("settings.backup_now")}</button>
            </div>
            {backupMsg && <div className={`notice ${backupMsg.ok ? "ok" : "error"}`}><pre>{backupMsg.text}</pre></div>}
          </section>
          <section className="panel">
            <h2>{t("settings.claude")}</h2>
            <p className="hint">{t("settings.claude_hint")}</p>
            <div className="row"><button className="primary" onClick={connectClaude}>{t("settings.claude_connect")}</button></div>
            {claudeMsg && <div className={`notice ${claudeMsg.ok ? "ok" : "error"}`}><pre>{claudeMsg.text}</pre></div>}
            {si && (
              <details>
                <summary className="small muted">{t("settings.claude_manual")}</summary>
                <pre className="raw">{JSON.stringify(si.claude_desktop, null, 2)}</pre>
              </details>
            )}
          </section>
          <details className="disclosure advanced" open={adv} onToggle={(e) => rememberAdv((e.target as HTMLDetailsElement).open)}>
            <summary><h2>{t("settings.advanced")}</h2><span className="hint">{t("settings.advanced_hint")}</span></summary>
            <div className="stack">
          <section className="panel">
            <h2>{t("settings.data_dir")}</h2>
            {si?.portable ? <p className="hint">{t("settings.portable_hint")}</p> : (
              <div className="stack-sm">
                <p className="hint">{t("settings.move_hint")}</p>
                <div className="row wrap">
                  <input className="grow" value={newDir} onChange={(e) => setNewDir(e.target.value)} placeholder={t("settings.move_placeholder")} />
                  <button onClick={() => moveDir(newDir.trim())} disabled={!newDir.trim()}>{t("settings.move")}</button>
                  {si && si.data_dir !== si.default_data_dir && <button className="ghost" onClick={() => moveDir(null)}>{t("settings.move_default")}</button>}
                </div>
              </div>
            )}
            {dirMsg && <div className={`notice ${dirMsg.ok ? "ok" : "error"}`}><pre>{dirMsg.text}</pre></div>}
            <label className="check"><input type="checkbox" checked={s.offline} onChange={(e) => patch({ offline: e.target.checked })} />
              {t("settings.offline")}</label>
            {!s.offline && (
              <div className="stack-sm">
                <label>{t("settings.contact_email")}
                  <input type="email" defaultValue={s.contact_email ?? ""} placeholder="name@university.edu"
                    onBlur={(e) => patch({ contact_email: e.target.value.trim() || null })} /></label>
                <p className="hint">{t("settings.contact_email_hint")}</p>
                {si && Object.entries(si.network.hosts).filter(([, h]) => h.failed > 0).map(([host, h]) => (
                  <div key={host} className="notice warn small">{t("settings.network_failing", { host, failed: h.failed, ok: h.ok })}
                    {h.last_error ? <div className="mono small">{h.last_error}</div> : null}</div>
                ))}
              </div>
            )}
          </section>
          <section className="panel">
            <h2>{t("settings.backups_dir")}</h2>
            {si && (
              <details>
                <summary className="small muted">{t("settings.restore_title")}</summary>
                <p className="hint">{t("settings.restore_hint")}</p>
                <pre className="raw">{`"${si.cli.join(" ")}" backups\n"${si.cli.join(" ")}" restore "<${t("settings.restore_file")}>"`}</pre>
              </details>
            )}
            <div className="row wrap">
              <input className="grow" value={backupDir} onChange={(e) => setBackupDir(e.target.value)} placeholder={t("settings.backups_dir_placeholder")} />
              <button onClick={() => patchChecked({ backup_dir: backupDir.trim() || null })}>{t("settings.save")}</button>
              {s.backup_dir && <button className="ghost" onClick={() => { setBackupDir(""); patchChecked({ backup_dir: null }); }}>{t("settings.move_default")}</button>}
            </div>
          </section>
          <section className="panel">
            <h2><SettingsIcon />{t("settings.models")}</h2>
            <p className="hint">{t("settings.models_hint")}</p>
            {si && !si.models_available && <div className="notice warn">{t("settings.models_not_installed")}</div>}
            <div className="form-grid">
              <label>{t("settings.embedder")}
                <select value={s.embedder} onChange={(e) => patch({ embedder: e.target.value })}>
                  <option value="hashing">{t("settings.builtin")}</option><option value="bge-m3" disabled={si ? !si.models_available : false}>bge-m3</option>
                </select>
                {s.embedder === "hashing" && <span className="hint">{t("settings.lexical_only")}</span>}</label>
              <label>{t("settings.reranker")}
                <select value={s.reranker} onChange={(e) => patch({ reranker: e.target.value })}>
                  <option value="lexical">{t("settings.builtin")}</option><option value="bge-reranker-v2-m3" disabled={si ? !si.models_available : false}>bge-reranker-v2-m3</option>
                </select></label>
              <label>{t("settings.nli")}
                <select value={s.nli} onChange={(e) => patch({ nli: e.target.value })}>
                  <option value="none">{t("settings.off")}</option><option value="mdeberta" disabled={si ? !si.models_available : false}>mDeBERTa-v3-base-xnli</option>
                </select></label>
              <label>{t("settings.inference")}
                <select value={s.inference_backend} onChange={(e) => patch({ inference_backend: e.target.value })}>
                  <option value="queue">{t("settings.inference_queue")}</option>
                  <option value="local" disabled={si ? !si.local_llm_available : false}>{t("settings.inference_local")}</option>
                  <option value="anthropic" disabled={si ? !si.anthropic_available : false}>{t("settings.inference_anthropic")}</option>
                </select></label>
            </div>
            {s.inference_backend === "anthropic" && (
              <div className="stack-sm anthropic">
                <p className="hint">{t("settings.anthropic_hint")}</p>
                <div className="form-grid">
                  <label>{t("settings.anthropic_model")}
                    <input defaultValue={s.anthropic_model} onBlur={(e) => patch({ anthropic_model: e.target.value.trim() || null })} /></label>
                  <label>{t("settings.anthropic_key")}
                    <input type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                      placeholder={keySource === "env" ? t("settings.anthropic_key_env") : keySource === "keyring" ? t("settings.anthropic_key_stored") : t("settings.anthropic_key_missing")} /></label>
                </div>
                <div className="row wrap">
                  <button className="primary" disabled={!apiKey.trim()} onClick={() => saveKey(apiKey.trim())}>{t("settings.anthropic_key_save")}</button>
                  {keySource === "keyring" && <button className="ghost" onClick={() => saveKey(null)}>{t("settings.anthropic_key_clear")}</button>}
                  <span className="hint">{keySource === "env" ? t("settings.anthropic_key_env") : keySource === "keyring" ? t("settings.anthropic_key_stored") : t("settings.anthropic_key_missing")}</span>
                </div>
                {keyMsg && <div className={`notice ${keyMsg.ok ? "ok" : "error"}`}>{keyMsg.text}</div>}
              </div>
            )}
            <div className="row wrap">
              <button onClick={rebuild.start} disabled={rebuild.running}>{rebuild.running ? t("jobs.running") : t("settings.rebuild")}</button>
              <span className="hint">{t("settings.rebuild_hint")}</span>
            </div>
            {rebuild.job?.status === "done" && (
              <div className="notice ok">
                {t("jobs.rebuild_done", { entities: rebuild.job.result?.entities ?? 0, seconds: rebuild.job.result?.seconds ?? 0 })}
                {(rebuild.job.result?.warnings ?? []).map((w: string) => <div key={w} className="small">{w}</div>)}
              </div>
            )}
            {rebuild.job?.status === "failed" && <div className="notice error"><pre>{rebuild.job.error}</pre></div>}
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
            </div>
          </details>
        </>
      )}
    </div>
  );
}
