// SPDX-License-Identifier: Apache-2.0
import { useTranslation } from "react-i18next";
import type { ImportResult } from "../api";
import { href } from "../router";
import { clearError, dismiss, repair, setMinimized, useImport } from "../importStore";
import { UploadIcon } from "./icons";

function Bar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
      <div style={{ width: `${pct}%` }} />
    </div>
  );
}

function ResultRow({ r }: { r: ImportResult }) {
  const { t } = useTranslation();
  const fixes = (r.repairs ?? []).map((c) => t(`home.fix.${c}`)).join("; ");
  return (
    <li className={r.ok ? "ok" : "failed"}>
      <div className="row between wrap">
        <strong className="grow">{r.ok ? "✓" : "✗"} {r.name}</strong>
        {r.ok && r.work_id ? <a href={href(`entity/${r.work_id}`)} onClick={() => void dismiss()}>{t(r.duplicate ? "import.duplicate" : "import.open")}</a> : null}
      </div>
      {fixes && <div className="small muted">{t("home.repaired", { fixes })}</div>}
      {!r.ok && r.report && <details><summary className="small">{t("home.show_report")}</summary><pre>{r.report}</pre></details>}
      {r.related?.length ? (
        <div className="small muted">{t("home.related")} {r.related.map((x) => x.title).join(" · ")}</div>
      ) : null}
    </li>
  );
}

/** The import in progress, above everything: the rest of the app is inert until it is dismissed. */
export default function ImportOverlay() {
  const { t } = useTranslation();
  const st = useImport();
  if (st.error && !st.uploading && !st.progress) {
    return (
      <div className="import-error notice error row wrap" role="alert">
        <span className="grow">{st.error === "not_rxf" ? t("home.drop_not_rxf") : st.error === "unreachable" ? t("common.unreachable") : st.error}</span>
        <button className="ghost small" onClick={clearError}>{t("import.close")}</button>
      </div>
    );
  }
  if (!st.uploading && !st.progress) return null;
  const p = st.progress;
  const finished = !!p && (p.status === "done" || p.status === "interrupted");
  const results = p ? Object.values(p.results) : [];
  const ok = results.filter((r) => r.ok).length;
  const repairable = results.filter((r) => !r.ok && r.repairable?.length).length;
  const title = st.uploading ? t("import.uploading") : finished ? t("import.finished") : p?.status === "queued" ? t("import.queued") : t("import.running");
  const line = st.uploading
    ? t("import.uploaded", { mb: (st.uploading.loaded / 1048576).toFixed(1), total: (st.uploading.total / 1048576).toFixed(1) })
    : p ? t("import.count", { done: p.done, total: p.total }) : "";

  if (st.minimized && !finished) {
    return (
      <div className="import-lock">
        <button className="import-pill" onClick={() => setMinimized(false)} title={t("import.expand")}>
          <UploadIcon /> <span>{title} · {line}</span>
          <Bar value={st.uploading ? st.uploading.loaded : p?.done ?? 0} max={st.uploading ? st.uploading.total : p?.total ?? 1} />
        </button>
      </div>
    );
  }
  return (
    <div className="import-lock">
      <div className="import-modal panel" role="dialog" aria-modal="true" aria-labelledby="import-title">
        <div className="row between">
          <h2 id="import-title"><UploadIcon />{title}</h2>
          {!finished && <button className="ghost small" onClick={() => setMinimized(true)}>{t("import.minimize")}</button>}
        </div>
        {!finished && (
          <>
            <Bar value={st.uploading ? st.uploading.loaded : p?.done ?? 0} max={st.uploading ? st.uploading.total : p?.total ?? 1} />
            <div className="row between small"><span>{line}</span>{p?.current && <span className="muted">{p.current}</span>}</div>
            <p className="hint">{t("import.locked_hint")}</p>
          </>
        )}
        {finished && p && (
          <>
            <p>{t("import.summary", { ok, failed: results.length - ok })}
              {p.skipped.length ? <span className="muted"> · {t("import.skipped", { n: p.skipped.length, files: p.skipped.slice(0, 4).join("、") })}</span> : null}</p>
            {p.status === "interrupted" && <div className="notice warn small">{t("import.interrupted")}{p.error ? `: ${p.error}` : ""}</div>}
            <ul className="plain import-results">{results.map((r) => <ResultRow key={r.name} r={r} />)}</ul>
            <div className="row wrap">
              <span className="grow" />
              {repairable > 0 && <button onClick={() => void repair()}>{t("import.repair", { n: repairable })}</button>}
              <button className="primary" autoFocus onClick={() => void dismiss()}>{t("import.done")}</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
