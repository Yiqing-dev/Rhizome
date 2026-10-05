// SPDX-License-Identifier: Apache-2.0
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError, errorText, type Hit } from "../api";
import { EmptyState, HitRow, Loading } from "../components/common";
import { SparkIcon, UploadIcon } from "../components/icons";
import { useLoad, useRefreshOnFocus } from "../hooks";
import { fmtNum } from "../i18n";
import { go, href } from "../router";

const RXF_HEAD = /^[ \t]*rxf_version[ \t]*:/m;
const TILE_TYPES = ["work", "dataset", "method", "idea", "claim", "topic"] as const;
const RECALL_MAX = 4000; // the server reads this much of the context

export default function Home() {
  const { t, i18n } = useTranslation();
  const stats = useLoad(() => api.stats(), []);
  const [q, setQ] = useState("");
  const [ctx, setCtx] = useState("");
  const [recalled, setRecalled] = useState<Hit[] | null>(null);
  const [recalling, setRecalling] = useState(false);
  const [recallErr, setRecallErr] = useState<string | null>(null);
  useRefreshOnFocus(stats.reload);
  async function doRecall() {
    if (recalling || !ctx.trim()) return;
    setRecalling(true);
    setRecallErr(null);
    try {
      setRecalled((await api.recall(ctx)).results);
    } catch (e) {
      setRecallErr(errorText(e, t));
    } finally {
      setRecalling(false);
    }
  }
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState<{ done: number; total: number } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [ingestMsg, setIngestMsg] = useState<{ ok: boolean; text: string; related?: any[]; retry?: File[]; repairable?: string[] } | null>(null);

  const failed = useLoad(() => api.inboxFailed(), []);
  const fixes = (codes: string[]) => codes.map((c) => t(`home.fix.${c}`)).join("; ");

  async function onFiles(list: FileList | File[] | null, repair = false) {
    if (busy) return;  // a second drop while importing would interleave two runs
    const files = Array.from(list ?? []);
    const stem = (n: string) => n.replace(/\.[^.]+$/, "").toLowerCase();
    const pdfs = new Map(files.filter((f) => /\.pdf$/i.test(f.name)).map((f) => [stem(f.name), f]));
    // RXF is recognised by content, not by name or extension (chat clients name files oddly)
    const rxf: File[] = [];
    for (const f of files) {
      if (/\.pdf$/i.test(f.name)) continue;
      if (/\.(ya?ml|rxf)$/i.test(f.name)) { rxf.push(f); continue; }
      try {
        if (RXF_HEAD.test(await f.slice(0, 65536).text())) rxf.push(f);
      } catch {
        /* a folder or an unreadable file: skipped */
      }
    }
    if (!rxf.length) {
      setIngestMsg({ ok: false, text: t("home.drop_not_rxf") });
      return;
    }
    const lines: string[] = [];
    let ok = true;
    let related: any[] = [];
    const retry: File[] = [];
    const repairable = new Set<string>();
    setBusy({ done: 0, total: rxf.length });
    try {
    for (const f of rxf) {
      setBusy({ done: lines.length, total: rxf.length });
      try {
        const r = await api.ingestFile(f, pdfs.get(stem(f.name)), repair);
        lines.push(t(r.duplicate ? "home.ingested_dup" : "home.ingested", { work: r.work_key }));
        if (r.repairs?.length) lines.push(t("home.repaired", { fixes: fixes(r.repairs) }));
        related = related.concat(r.related ?? []);
      } catch (e) {
        ok = false;
        const d = (e as ApiError).detail as { report?: string; repairable?: string[] } | string | undefined;
        lines.push(`${f.name}: ${(typeof d === "string" ? d : d?.report) ?? t("common.error")}`);
        if (typeof d === "object" && d?.repairable?.length) {
          retry.push(f, ...(pdfs.has(stem(f.name)) ? [pdfs.get(stem(f.name))!] : []));
          d.repairable.forEach((c) => repairable.add(c));
        }
      }
    }
    setIngestMsg({ ok, text: lines.join("\n\n"), related: [...new Map(related.map((r) => [r.work_id, r])).values()],
                   retry: retry.length ? retry : undefined, repairable: [...repairable] });
    stats.reload();
    } finally {
      setBusy(null);
    }
  }

  async function retryFailed(name: string, repair: boolean) {
    try {
      const r = await api.inboxRetry(name, repair);
      const lines = [t("home.ingested", { work: r.work_key })];
      if (r.repairs?.length) lines.push(t("home.repaired", { fixes: fixes(r.repairs) }));
      setIngestMsg({ ok: true, text: lines.join("\n"), related: r.related });
      stats.reload();
    } catch (e) {
      const d = (e as ApiError).detail as { report?: string } | undefined;
      setIngestMsg({ ok: false, text: d?.report ?? t("common.error") });
    }
    failed.reload();
  }

  const s = stats.data;
  const total = s ? Object.values(s.entities).reduce((a, b) => a + b, 0) : 0;
  return (
    <div className="stack">
      <section className="hero">
        <h1>{t("home.tagline")}</h1>
        <p>{t("home.tagline_sub")}</p>
        <form className="searchbar" onSubmit={(e) => { e.preventDefault(); if (q.trim()) go("search", { q }); }}>
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("search.placeholder")} />
          <button type="submit" className="primary">{t("search.go")}</button>
        </form>
      </section>

      <Loading error={stats.error} loading={stats.loading && !s} />
      {s && total === 0 && (
        <section className="steps">
          <div className="step"><strong>{t("home.step1_t")}</strong>{t("home.step1")}{" "}
            <a href={href("settings")}>{t("settings.project")} →</a></div>
          <div className="step"><strong>{t("home.step2_t")}</strong>{t("home.step2")}</div>
          <div className="step"><strong>{t("home.step3_t")}</strong>{t("home.step3")}</div>
        </section>
      )}
      {s && total > 0 && (
        <section className="tiles">
          {TILE_TYPES.map((k) => (
            <a className={`tile t-${k}`} key={k} href={href(`search?types=${k}`)}>
              <div className="num">{fmtNum(s.entities[k] ?? 0, i18n.language)}</div>
              <div className="lbl">{t(`type.${k}`)}</div>
            </a>
          ))}
        </section>
      )}
      {s && total > 0 && (s.cards_due > 0 || s.review_queue > 0) && (
        <p className="todo">
          {s.cards_due > 0 && <a href={href("cards")}>{t("home.todo_cards", { n: fmtNum(s.cards_due, i18n.language) })}</a>}
          {s.review_queue > 0 && <a href={href("review")}>{t("home.todo_queue", { n: fmtNum(s.review_queue, i18n.language) })}</a>}
        </p>
      )}

      <div className="stack">
        <section className="panel">
          <h2><UploadIcon />{t("home.ingest_title")}</h2>
          <div className="row between wrap">
            <p className="hint grow">{t("home.ingest_hint")}</p>
            <button onClick={() => api.openFolder("inbox")}>{t("home.open_inbox")}</button>
          </div>
          <label className={`drop ${over ? "over" : ""} ${busy ? "busy" : ""}`} aria-busy={!!busy} tabIndex={0} role="button"
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.current?.click(); } }}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); onFiles(e.dataTransfer.files); }}>
            <input ref={fileInput} type="file" multiple hidden onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }} />
            <UploadIcon />
            <span>{busy ? t("home.importing", { done: busy.done, total: busy.total }) : t("home.drop")}</span>
          </label>
          {ingestMsg && (
            <div className={`notice ${ingestMsg.ok ? "ok" : "error"}`}>
              <pre>{ingestMsg.text}</pre>
              {ingestMsg.retry && (
                <div className="row wrap" style={{ marginTop: "0.5rem" }}>
                  <span className="grow" />
                  <button className="primary" title={t("home.repairable", { fixes: fixes(ingestMsg.repairable ?? []) })}
                    onClick={() => onFiles(ingestMsg.retry!, true)}>{t("home.repair_import")}</button>
                </div>
              )}
              {ingestMsg.related?.length ? (
                <div className="stack-sm" style={{ marginTop: "0.5rem" }}>
                  <div className="eyebrow">{t("home.related")}</div>
                  <ul className="plain related">{ingestMsg.related.map((r) => (
                    <li key={r.work_id}>
                      <a href={href(`entity/${r.work_id}`)}>{r.title}</a>
                      <div className="via">{Array.from(new Set<string>(r.via.map((v: any) => t(`type.${v.dimension}`)))).map((d) => <span key={d}>{d}</span>)}</div>
                    </li>
                  ))}</ul>
                </div>
              ) : null}
            </div>
          )}
          {s?.vocab?.stale ? <p className="hint">{t("home.vocab_stale")} <a href={href("settings")}>{t("settings.vocab_download")}</a></p> : null}
          {s?.inbox?.pending.length ? <p className="hint">{t("home.inbox_pending", { n: s.inbox.pending.length, files: s.inbox.pending.slice(0, 5).join("、") })}</p> : null}
          {s?.inbox?.ignored.length ? <p className="hint">{t("home.inbox_ignored", { n: s.inbox.ignored.length, files: s.inbox.ignored.slice(0, 5).join("、") })}</p> : null}
          {failed.data?.files.length ? (
            <div className="stack-sm failed-inbox">
              <div className="eyebrow">{t("home.failed_title")}</div>
              <p className="hint">{t("home.failed_hint")}</p>
              <ul className="plain">{failed.data.files.map((f) => (
                <li key={f.name}>
                  <div className="row between wrap">
                    <strong className="grow">{f.name}</strong>
                    <button onClick={() => retryFailed(f.name, false)}>{t("home.retry")}</button>
                    {f.repairable.length ? <button className="primary" title={fixes(f.repairable)}
                      onClick={() => retryFailed(f.name, true)}>{t("home.repair_import")}</button> : null}
                  </div>
                  {f.report && <details><summary>{t("home.show_report")}</summary><pre>{f.report}</pre></details>}
                </li>
              ))}</ul>
            </div>
          ) : null}
        </section>
        <details className="panel disclosure">
          <summary><h2><SparkIcon />{t("home.recall_title")}</h2><span className="hint">{t("home.recall_summary")}</span></summary>
          <p className="hint">{t("home.recall_hint")}</p>
          <textarea rows={5} value={ctx} onChange={(e) => setCtx(e.target.value)} placeholder={t("home.recall_placeholder")} />
          <div className="row between wrap">
            <button className="primary" onClick={doRecall} disabled={!ctx.trim() || recalling} aria-busy={recalling}>
              {recalling ? t("home.recalling") : t("home.recall_go")}
            </button>
            <span className={`muted small ${ctx.length > RECALL_MAX ? "warn-text" : ""}`}>
              {t("home.recall_chars", { n: ctx.length, max: RECALL_MAX })}
            </span>
          </div>
          {recallErr && <div className="notice error">{recallErr}</div>}
          {recalled && (recalled.length
            ? <ul className="hits">{recalled.map((h) => <HitRow key={h.id} h={h} score={h.score} />)}</ul>
            : <EmptyState title={t("common.no_results")} hint={t("home.recall_empty_hint")} />)}
        </details>
      </div>
    </div>
  );
}
