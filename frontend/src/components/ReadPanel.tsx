// SPDX-License-Identifier: Apache-2.0
// "Let Claude read a new paper": drop PDFs, one job per file runs on the server (identify, check the
// library, review, export RXF, validate, import). The panel follows the jobs it started plus any
// read_paper job still running from before (another window, a reload). On the Home sheet it is the
// lower half of the capture column: a title, a line, a "Choose PDF…" button that also takes drops.
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError, errorText, type JobView, type ReadResult, type ReaderStatus } from "../api";
import { href } from "../router";
import { SparkIcon } from "./icons";

const POLL_MS = 2000;

export default function ReadPanel({ status, onChanged }: { status?: ReaderStatus | null; onChanged?: () => void }) {
  const { t } = useTranslation();
  const [jobs, setJobs] = useState<JobView[]>([]);
  const [over, setOver] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const enabled = !!status?.enabled;

  useEffect(() => {  // reads still running from before this page was opened
    api.activeJobs().then((r) => setJobs((cur) => merge(cur, r.jobs.filter((j) => j.kind === "read_paper")))).catch(() => undefined);
  }, []);

  useEffect(() => {  // follow the live ones
    const live = jobs.filter((j) => j.status === "queued" || j.status === "running");
    if (!live.length) return;
    const timer = window.setTimeout(async () => {
      const fresh = await Promise.all(live.map((j) => api.job(j.id).catch(() => j)));
      setJobs((cur) => merge(cur, fresh));
      if (fresh.some((j) => j.status === "done" || j.status === "failed")) changed.current?.();
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [jobs]);

  async function onFiles(list: FileList | File[] | null) {
    const files = Array.from(list ?? []);
    if (!files.length) return;
    setMsg(null);
    try {
      const r = await api.readPapers(files);
      setJobs((cur) => merge(cur, r.jobs));
      if (r.skipped.length) setMsg({ ok: true, text: t("home.read_skipped", { files: r.skipped.join(", ") }) });
    } catch (e) {
      const d = (e as ApiError).detail as { code?: string } | undefined;
      setMsg({ ok: false, text: d?.code === "not_pdf" ? t("home.read_not_pdf") : errorText(e, t) });
    }
  }

  async function force(j: JobView) {
    try {
      const nj = await api.readForce(j.id);
      setJobs((cur) => merge(cur.filter((x) => x.id !== j.id), [nj]));
    } catch (e) {
      setMsg({ ok: false, text: errorText(e, t) });
    }
  }

  const why = status?.reason === "no_key" ? t("home.read_disabled_no_key")
    : status?.reason === "sdk_missing" ? t("home.read_disabled_sdk") : t("home.read_disabled_backend");
  return (
    <div className={`sub read-panel ${over ? "over" : ""}`}
      onDragOver={(e) => { if (enabled) { e.preventDefault(); setOver(true); } }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { if (!enabled) return; e.preventDefault(); setOver(false); onFiles(e.dataTransfer.files); }}>
      <div className="h"><SparkIcon />{t("home.read_title")}</div>
      <p className="hint">{t("home.read_blurb")}</p>
      {!enabled && <p className="hint">{why} <a href={href("settings")}>{t("nav.settings")} →</a></p>}
      {enabled && (
        <div>
          <input ref={fileInput} type="file" accept="application/pdf,.pdf" multiple hidden onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }} />
          <button type="button" className={`btn ${over ? "primary" : ""}`} onClick={() => fileInput.current?.click()} title={t("home.read_drop")}>
            {t("home.read_choose")}
          </button>
        </div>
      )}
      {msg && <div className={`notice ${msg.ok ? "ok" : "error"}`}>{msg.text}</div>}
      {jobs.length > 0 && (
        <ul className="plain reads">
          {jobs.slice().sort((a, b) => b.id - a.id).map((j) => <ReadRow key={j.id} job={j} onForce={() => force(j)} />)}
        </ul>
      )}
    </div>
  );
}

function ReadRow({ job, onForce }: { job: JobView; onForce: () => void }) {
  const { t } = useTranslation();
  const name = String(job.payload?.name ?? "");
  const live = job.status === "queued" || job.status === "running";
  const step = job.status === "queued" ? "queued" : String((job as JobView & { progress?: { step?: string } }).progress?.step ?? "identify");
  const r = (job.result ?? null) as ReadResult | null;
  const title = r?.paper?.title ? r.paper.title : name;
  return (
    <li className={`read-row ${job.status}`}>
      <div className="row between wrap">
        <div className="grow">
          <strong>{title}</strong>
          {r?.paper?.title && <span className="muted small"> · {name}</span>}
        </div>
        {live && <span className="pill busy">{t(`home.read_step.${step}`, { defaultValue: step })}</span>}
        {job.status === "failed" && <span className="pill warn">{t("home.read_failed")}</span>}
        {r?.status === "done" && <span className="pill ok">{t("home.read_done")}</span>}
        {r?.status === "duplicate" && <span className="pill">{t("home.read_duplicate")}</span>}
        {r?.status === "invalid" && <span className="pill warn">{t("home.read_invalid")}</span>}
      </div>
      {live && <div className="progress indeterminate"><i /></div>}
      {r?.status === "done" && r.work?.id != null && (
        <div className="row wrap small">
          <a href={href(`entity/${r.work.id}`)}>{t("home.read_show_report")} →</a>
          {r.usage && <span className="muted">{t("home.read_tokens", { input: r.usage.input, output: r.usage.output })}</span>}
        </div>
      )}
      {r?.status === "duplicate" && r.work?.id != null && (
        <div className="row wrap small">
          <a href={href(`entity/${r.work.id}`)}>{r.work.name ?? r.work.key}</a>
          <button className="btn" onClick={onForce}>{t("home.read_force")}</button>
        </div>
      )}
      {r?.status === "invalid" && (
        <details><summary className="small">{t("home.read_show_errors")}</summary><pre>{r.errors}</pre></details>
      )}
      {job.status === "failed" && job.error && <pre className="small">{job.error.split("\n")[0]}</pre>}
    </li>
  );
}

function merge(cur: JobView[], incoming: JobView[]): JobView[] {
  const byId = new Map(cur.map((j) => [j.id, j]));
  for (const j of incoming) byId.set(j.id, j);
  return Array.from(byId.values());
}
