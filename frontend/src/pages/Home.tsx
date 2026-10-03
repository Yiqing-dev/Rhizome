// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError, type Hit } from "../api";
import { HitRow, Loading } from "../components/common";
import { useLoad } from "../hooks";
import { fmtNum } from "../i18n";
import { go, href } from "../router";

export default function Home() {
  const { t, i18n } = useTranslation();
  const stats = useLoad(() => api.stats(), []);
  const [q, setQ] = useState("");
  const [ctx, setCtx] = useState("");
  const [recalled, setRecalled] = useState<Hit[] | null>(null);
  const [ingestMsg, setIngestMsg] = useState<{ ok: boolean; text: string; related?: any[] } | null>(null);

  async function onFile(f: File) {
    try {
      const r = await api.ingest(await f.text(), f.name);
      setIngestMsg({ ok: true, text: t("home.ingested", { work: r.work_key }), related: r.related });
      stats.reload();
    } catch (e) {
      const d = (e as ApiError).detail as { report?: string } | undefined;
      setIngestMsg({ ok: false, text: d?.report ?? t("common.error") });
    }
  }

  const s = stats.data;
  return (
    <div className="stack">
      <form className="searchbar" onSubmit={(e) => { e.preventDefault(); go("search", { q }); }}>
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("search.placeholder")} />
        <button type="submit">{t("search.go")}</button>
      </form>
      <Loading error={stats.error} loading={stats.loading && !s} />
      {s && (
        <section className="tiles">
          {(["work", "dataset", "method", "idea", "claim", "topic"] as const).map((k) => (
            <div className="tile" key={k}>
              <div className="num">{fmtNum(s.entities[k] ?? 0, i18n.language)}</div>
              <div className="muted">{t(`type.${k}`)}</div>
            </div>
          ))}
          <a className={`tile ${s.review_queue > 0 ? "attention" : ""}`} href={href("review")}>
            <div className="num">{fmtNum(s.review_queue, i18n.language)}</div>
            <div className="muted">{t("home.queue")}</div>
          </a>
          <a className="tile" href={href("cards")}>
            <div className="num">{fmtNum(s.cards_due, i18n.language)}</div>
            <div className="muted">{t("home.cards_due")}</div>
          </a>
        </section>
      )}
      <section className="panel">
        <h2>{t("home.ingest_title")}</h2>
        <p className="muted">{t("home.ingest_hint")}</p>
        <label className="drop"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) onFile(f); }}>
          <input type="file" accept=".yaml,.yml" hidden onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
          {t("home.drop")}
        </label>
        {ingestMsg && (
          <div className={ingestMsg.ok ? "ok" : "error"}>
            <pre>{ingestMsg.text}</pre>
            {ingestMsg.related?.length ? (
              <>
                <div className="muted">{t("home.related")}</div>
                <ul>{ingestMsg.related.map((r) => (
                  <li key={r.work_id}><a href={href(`entity/${r.work_id}`)}>{r.title}</a>{" "}
                    <span className="muted">· {Array.from(new Set(r.via.map((v: any) => t(`type.${v.dimension}`)))).join(" / ")}</span></li>
                ))}</ul>
              </>
            ) : null}
          </div>
        )}
      </section>
      <section className="panel">
        <h2>{t("home.recall_title")}</h2>
        <textarea rows={4} value={ctx} onChange={(e) => setCtx(e.target.value)} placeholder={t("home.recall_placeholder")} />
        <button onClick={async () => setRecalled((await api.recall(ctx)).results)} disabled={!ctx.trim()}>
          {t("home.recall_go")}
        </button>
        {recalled && (recalled.length ? <ul className="hits">{recalled.map((h) => <HitRow key={h.id} h={h} />)}</ul>
          : <p className="muted">{t("common.no_results")}</p>)}
      </section>
    </div>
  );
}
