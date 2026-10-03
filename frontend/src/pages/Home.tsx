// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError, type Hit } from "../api";
import { EmptyState, HitRow, Loading } from "../components/common";
import { SparkIcon, UploadIcon } from "../components/icons";
import { useLoad } from "../hooks";
import { fmtNum } from "../i18n";
import { go, href } from "../router";

const TILE_TYPES = ["work", "dataset", "method", "idea", "claim", "topic"] as const;

export default function Home() {
  const { t, i18n } = useTranslation();
  const stats = useLoad(() => api.stats(), []);
  const [q, setQ] = useState("");
  const [ctx, setCtx] = useState("");
  const [recalled, setRecalled] = useState<Hit[] | null>(null);
  const [over, setOver] = useState(false);
  const [ingestMsg, setIngestMsg] = useState<{ ok: boolean; text: string; related?: any[] } | null>(null);

  async function onFiles(list: FileList | null) {
    const files = Array.from(list ?? []);
    const stem = (n: string) => n.replace(/\.[^.]+$/, "").toLowerCase();
    const rxf = files.filter((f) => /\.ya?ml$/i.test(f.name));
    if (!rxf.length) {
      setIngestMsg({ ok: false, text: t("home.drop_not_rxf") });
      return;
    }
    const pdfs = new Map(files.filter((f) => /\.pdf$/i.test(f.name)).map((f) => [stem(f.name), f]));
    const lines: string[] = [];
    let ok = true;
    let related: any[] = [];
    for (const f of rxf) {
      try {
        const r = await api.ingestFile(f, pdfs.get(stem(f.name)));
        lines.push(t(r.duplicate ? "home.ingested_dup" : "home.ingested", { work: r.work_key }));
        related = related.concat(r.related ?? []);
      } catch (e) {
        ok = false;
        const d = (e as ApiError).detail as { report?: string } | string | undefined;
        lines.push(`${f.name}: ${(typeof d === "string" ? d : d?.report) ?? t("common.error")}`);
      }
    }
    setIngestMsg({ ok, text: lines.join("\n\n"), related: [...new Map(related.map((r) => [r.work_id, r])).values()] });
    stats.reload();
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
          <div className="step"><strong>{t("home.step1_t")}</strong>{t("home.step1")}</div>
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
          <a className={`tile ${s.review_queue > 0 ? "attention" : ""}`} href={href("review")}>
            <div className="num">{fmtNum(s.review_queue, i18n.language)}</div>
            <div className="lbl">{t("home.queue")}</div>
          </a>
          <a className="tile" href={href("cards")}>
            <div className="num">{fmtNum(s.cards_due, i18n.language)}</div>
            <div className="lbl">{t("home.cards_due")}</div>
          </a>
        </section>
      )}

      <div className="grid-2">
        <section className="panel">
          <h2><UploadIcon />{t("home.ingest_title")}</h2>
          <div className="row between wrap">
            <p className="hint grow">{t("home.ingest_hint")}</p>
            <button onClick={() => api.openFolder("inbox")}>{t("home.open_inbox")}</button>
          </div>
          <label className={`drop ${over ? "over" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); onFiles(e.dataTransfer.files); }}>
            <input type="file" accept=".yaml,.yml,.pdf" multiple hidden onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }} />
            <UploadIcon />
            <span>{t("home.drop")}</span>
          </label>
          {ingestMsg && (
            <div className={`notice ${ingestMsg.ok ? "ok" : "error"}`}>
              <pre>{ingestMsg.text}</pre>
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
        </section>
        <section className="panel">
          <h2><SparkIcon />{t("home.recall_title")}</h2>
          <p className="hint">{t("home.recall_hint")}</p>
          <textarea rows={5} value={ctx} onChange={(e) => setCtx(e.target.value)} placeholder={t("home.recall_placeholder")} />
          <div className="row">
            <button className="primary" onClick={async () => setRecalled((await api.recall(ctx)).results)} disabled={!ctx.trim()}>
              {t("home.recall_go")}
            </button>
          </div>
          {recalled && (recalled.length
            ? <ul className="hits">{recalled.map((h) => <HitRow key={h.id} h={h} score={h.score} />)}</ul>
            : <EmptyState title={t("common.no_results")} hint={t("home.recall_empty_hint")} />)}
        </section>
      </div>
    </div>
  );
}
