// SPDX-License-Identifier: Apache-2.0
// Home, the "Ledger": one ruled sheet in the Modernist system. Search with live results and
// today's numbers on top, the six asset counts, then capture, the newest papers and recall side
// by side. Everything is real data from /stats, /search and /recall.
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError, errorText, type Hit, type RecentWork } from "../api";
import { EmptyState, entityHref, Loading } from "../components/common";
import { ArrowIcon, FolderIcon, SearchIcon, SparkIcon, UploadIcon } from "../components/icons";
import ReadPanel from "../components/ReadPanel";
import { useLoad, useRefreshOnFocus } from "../hooks";
import { fmtDate, fmtNum, parseUtc } from "../i18n";
import { go, href } from "../router";
import { start as startImport, useImport } from "../importStore";

const TILE_TYPES = ["work", "dataset", "method", "idea", "claim", "topic"] as const;
const COUNT_TYPES = ["dataset", "method", "idea", "claim"] as const;
const RECALL_MAX = 4000; // the server reads this much of the context
const LIVE_RESULTS = 6;
const LIVE_DEBOUNCE_MS = 180;

/** "Today 14:02", "Yesterday", "Sat 24 Oct", or a dated line for older captures. */
function fmtWhen(iso: string | null, lng: string, t: (k: string, o?: Record<string, unknown>) => string): string {
  if (!iso) return "";
  const d = parseUtc(iso);
  const now = new Date();
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((day(now) - day(d)) / 86400000);
  if (days === 0) return t("home.when_today", { time: new Intl.DateTimeFormat(lng, { hour: "2-digit", minute: "2-digit" }).format(d) });
  if (days === 1) return t("home.when_yesterday");
  if (days < 7) return new Intl.DateTimeFormat(lng, { weekday: "short", day: "numeric", month: "short" }).format(d);
  return fmtDate(iso, lng);
}

function fmtSeen(days: number | null | undefined, t: (k: string, o?: Record<string, unknown>) => string): string {
  if (days === null || days === undefined) return "";
  if (days < 1) return t("home.seen_today");
  if (days < 2) return t("home.seen_yesterday");
  if (days < 30) return t("home.seen_days", { n: Math.round(days) });
  return t("home.seen_months", { n: Math.max(1, Math.round(days / 30)) });
}

/** The search box with a live dropdown: the first results as you type, Enter for the full page. */
function LiveSearch() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(-1);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const term = q.trim();
    if (!term) { setHits([]); setOpen(false); return; }
    let live = true;
    const timer = window.setTimeout(() => {
      api.search(term, { limit: LIVE_RESULTS }).then((r) => {
        if (!live) return;
        setHits(r.results);
        setSel(-1);
        setOpen(r.results.length > 0);
      }).catch(() => { if (live) setOpen(false); });
    }, LIVE_DEBOUNCE_MS);
    return () => { live = false; window.clearTimeout(timer); };
  }, [q]);
  useEffect(() => {  // a click elsewhere closes the dropdown
    if (!open) return;
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (!open || !hits.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => (s + 1) % hits.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => (s <= 0 ? hits.length - 1 : s - 1)); }
    else if (e.key === "Escape") { setOpen(false); }
    else if (e.key === "Enter" && sel >= 0) { e.preventDefault(); window.location.hash = entityHref(hits[sel]); }
  }
  return (
    <div className="home-search" ref={box}>
      <form role="search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) go("search", { q }); }}>
        <div className="box">
          <SearchIcon />
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} onFocus={() => hits.length && setOpen(true)}
            placeholder={t("search.placeholder")} aria-label={t("nav.search")} aria-expanded={open} aria-autocomplete="list" />
          <kbd aria-hidden="true">/</kbd>
        </div>
        <button type="submit" className="go">{t("search.go")}<ArrowIcon /></button>
      </form>
      {open && (
        <div className="live" role="listbox">
          {hits.map((h, i) => {
            const s = h.sources[0];
            return (
              <a key={h.id} href={entityHref(h)} role="option" aria-selected={i === sel} className={i === sel ? "on" : ""}
                onMouseEnter={() => setSel(i)}>
                <span className="label">{t(`type.${h.type}`)}</span>
                <span className="col">
                  <span className="name">{h.name}</span>
                  <span className="src">{s?.title ?? h.external_id ?? ""}</span>
                </span>
                <span className="yr">{s?.year ?? ""}</span>
              </a>
            );
          })}
          <div className="foot">{t("home.search_more")}</div>
        </div>
      )}
    </div>
  );
}

export default function Home() {
  const { t, i18n } = useTranslation();
  const lng = i18n.language;
  const stats = useLoad(() => api.stats(), []);
  useRefreshOnFocus(stats.reload);
  const s = stats.data;
  const total = s ? Object.values(s.entities).reduce((a, b) => a + b, 0) : 0;
  const days = s?.week_days ?? 7;

  // Claude's suggestions on the review queue (the agent summary rides on the queue's first page)
  const [suggested, setSuggested] = useState(0);
  useEffect(() => {
    if (!s || s.review_queue === 0) { setSuggested(0); return; }
    api.review(undefined, 0).then((r) => setSuggested(r.agent?.enabled ? r.agent.applicable ?? 0 : 0)).catch(() => setSuggested(0));
  }, [s?.review_queue]);  // eslint-disable-line react-hooks/exhaustive-deps

  // recall
  const [ctx, setCtx] = useState("");
  const [recalled, setRecalled] = useState<Hit[] | null>(null);
  const [recalling, setRecalling] = useState(false);
  const [recallErr, setRecallErr] = useState<string | null>(null);
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

  // capture
  const [over, setOver] = useState(false);
  const imp = useImport();
  const busy = !!imp.uploading || !!imp.progress;
  const fileInput = useRef<HTMLInputElement>(null);
  const [ingestMsg, setIngestMsg] = useState<{ ok: boolean; text: string; related?: any[] } | null>(null);
  const failed = useLoad(() => api.inboxFailed(), []);
  const fixes = (codes: string[]) => codes.map((c) => t(`home.fix.${c}`)).join("; ");
  function onFiles(list: FileList | File[] | null) {
    setIngestMsg(null);
    startImport(Array.from(list ?? []));  // progress, results and repair live in the import overlay
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

  const today = new Intl.DateTimeFormat(lng, { weekday: "long", day: "numeric", month: "long" }).format(new Date());
  const pending = s?.inbox?.pending.length ?? 0;
  const counts = (w: RecentWork) =>
    COUNT_TYPES.filter((k) => (w.counts[k] ?? 0) > 0).map((k) => t(`home.count.${k}`, { count: w.counts[k] })).join(" · ");

  return (
    <div className="home">
      <div className="home-top">
        <section className="home-hero">
          <div className="kicker">{today}</div>
          <h1>{t("home.tagline")}</h1>
          <LiveSearch />
        </section>
        <section className="home-today" aria-label={t("home.today")}>
          <div className="h">{t("home.today")}</div>
          <a href={href("cards")}>
            <span className="n">{fmtNum(s?.cards_due ?? 0, lng)}</span>
            <span><span className="t">{t("home.today_cards")}</span><br /><span className="s">{s?.cards_due ? t("home.today_cards_sub") : t("home.today_cards_none")}</span></span>
            <ArrowIcon />
          </a>
          <a href={href("review")}>
            <span className="n">{fmtNum(s?.review_queue ?? 0, lng)}</span>
            <span><span className="t">{t("home.today_queue")}</span><br /><span className="s">
              {suggested > 0 ? t("home.today_queue_agent", { n: suggested }) : s?.review_queue ? t("home.today_queue_sub") : t("home.today_queue_none")}
            </span></span>
            <ArrowIcon />
          </a>
          <a href={href("search?types=work")}>
            <span className="n">{fmtNum(s?.week?.work ?? 0, lng)}</span>
            <span><span className="t">{t("home.today_week", { days })}</span><br /><span className="s">{t("home.today_week_sub")}</span></span>
            <ArrowIcon />
          </a>
        </section>
      </div>

      <Loading error={stats.error} loading={stats.loading && !s} skeleton="tiles" />
      {s && (
        <section className="home-tiles">
          {TILE_TYPES.map((k) => {
            const n = s.week?.[k] ?? 0;
            return (
              <a key={k} href={href(`search?types=${k}`)}>
                <span className="label">{t(`home.tile.${k}`)}</span>
                <span className="n">{fmtNum(s.entities[k] ?? 0, lng)}</span>
                <span className={`d ${n ? "" : "flat"}`}>{n ? t("home.tile_delta", { n: fmtNum(n, lng), days }) : t("home.tile_flat", { days })}</span>
              </a>
            );
          })}
        </section>
      )}

      <div className="home-cols">
        <section className="home-col">
          <div className="h"><UploadIcon />{t("home.ingest_title")}</div>
          <p className="hint">{t("home.ingest_hint")}</p>
          <label className={`drop ${over ? "over" : ""} ${busy ? "busy" : ""}`} aria-busy={!!busy} tabIndex={0} role="button"
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.current?.click(); } }}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); onFiles(e.dataTransfer.files); }}>
            <input ref={fileInput} type="file" multiple hidden onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }} />
            <b>{busy ? t("import.running") : t("home.drop_title")}</b>
            <span>{t("home.drop_sub")}</span>
          </label>
          {ingestMsg && (
            <div className={`notice ${ingestMsg.ok ? "ok" : "error"}`}>
              <pre>{ingestMsg.text}</pre>
              {ingestMsg.related?.length ? (
                <div className="list" style={{ marginTop: "8px" }}>
                  <div className="label">{t("home.related")}</div>
                  {ingestMsg.related.map((r) => (
                    <a key={r.work_id} href={href(`entity/${r.work_id}`)}>
                      <span className="t">{r.title}</span>
                      <span className="m">{Array.from(new Set<string>(r.via.map((v: any) => t(`type.${v.dimension}`)))).map((d) => <span key={d}>{d}</span>)}</span>
                    </a>
                  ))}
                </div>
              ) : null}
            </div>
          )}
          <div className="between">
            <button className="btn" onClick={() => api.openFolder("inbox")}><FolderIcon />{t("home.open_inbox")}</button>
            <span className="inbox-state">{pending ? t("home.inbox_waiting", { n: pending }) : t("home.inbox_empty")}</span>
          </div>
          {s?.vocab?.stale ? <p className="hint">{t("home.vocab_stale")} <a href={href("settings")}>{t("settings.vocab_download")}</a></p> : null}
          {pending ? <p className="hint">{t("home.inbox_pending", { n: pending, files: s!.inbox!.pending.slice(0, 5).join("、") })}</p> : null}
          {s?.inbox?.ignored.length ? <p className="hint">{t("home.inbox_ignored", { n: s.inbox.ignored.length, files: s.inbox.ignored.slice(0, 5).join("、") })}</p> : null}
          {failed.data?.files.length ? (
            <div className="sub">
              <div className="label">{t("home.failed_title")}</div>
              <p className="hint">{t("home.failed_hint")}</p>
              <ul className="plain failed">{failed.data.files.map((f) => (
                <li key={f.name}>
                  <strong>{f.name}</strong>
                  <div className="between" style={{ justifyContent: "flex-start" }}>
                    <button className="btn" onClick={() => retryFailed(f.name, false)}>{t("home.retry")}</button>
                    {f.repairable.length ? <button className="btn primary" title={fixes(f.repairable)}
                      onClick={() => retryFailed(f.name, true)}>{t("home.repair_import")}</button> : null}
                  </div>
                  {f.report && <details><summary>{t("home.show_report")}</summary><pre>{f.report}</pre></details>}
                </li>
              ))}</ul>
            </div>
          ) : null}
          <ReadPanel status={s?.reader} onChanged={stats.reload} />
        </section>

        <section className="home-col">
          <div className="between">
            <span className="h">{t("home.recent_title")}</span>
            {s?.recent?.length ? <a className="quiet" href={href("search?types=work")}>{t("home.see_all")}</a> : null}
          </div>
          {s && total === 0 && (
            <div className="steps">
              <div className="step"><strong>{t("home.step1_t")}</strong>{t("home.step1")}{" "}
                <a href={href("settings")}>{t("settings.project")} →</a></div>
              <div className="step"><strong>{t("home.step2_t")}</strong>{t("home.step2")}</div>
              <div className="step"><strong>{t("home.step3_t")}</strong>{t("home.step3")}</div>
            </div>
          )}
          {s?.recent?.length ? (
            <div className="list">
              {s.recent.map((w) => (
                <a key={w.id} href={href(`entity/${w.id}`)}>
                  <span className="t">{w.name}</span>
                  <span className="m">
                    <b>{t(w.via === "reader" ? "home.via_reader" : "home.via_rxf")}</b>
                    <span>{fmtWhen(w.created_at, lng, t)}</span>
                    {w.depth ? <><span>·</span><span>{t(`home.depth.${w.depth}`)}</span></> : null}
                  </span>
                  {counts(w) && <span className="c">{counts(w)}</span>}
                </a>
              ))}
            </div>
          ) : null}
        </section>

        <section className="home-col">
          <div className="h"><SparkIcon />{t("home.recall_title")}</div>
          <p className="hint">{t("home.recall_hint")}</p>
          <textarea rows={4} value={ctx} onChange={(e) => setCtx(e.target.value)} placeholder={t("home.recall_placeholder")}
            onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); void doRecall(); } }} />
          <div className="between">
            <button className="btn primary" onClick={doRecall} disabled={!ctx.trim() || recalling} aria-busy={recalling}>
              {recalling ? t("home.recalling") : t("home.recall_go")}
            </button>
            <span className={`chars ${ctx.length > RECALL_MAX ? "warn-text" : ""}`}>
              {t("home.recall_chars", { n: ctx.length, max: RECALL_MAX })}
            </span>
          </div>
          {recallErr && <div className="notice error">{recallErr}</div>}
          {recalled && (recalled.length
            ? (
              <div className="recalls">
                {recalled.map((h) => {
                  const rel = h.relevance ?? h.score ?? 0;
                  return (
                    <a key={h.id} href={entityHref(h)}>
                      <span className="top">
                        <span className="label">{t(`type.${h.type}`)}</span>
                        <span className="name">{h.name}{h.origin === "user" ? <> <span className="pill user">{t("origin.user")}</span></> : null}</span>
                      </span>
                      <span className="bottom">
                        <span className="bar"><i style={{ width: `${Math.round(Math.max(0, Math.min(1, rel)) * 100)}%` }} /></span>
                        <span className="rel">{rel.toFixed(2)}</span>
                        <span>{fmtSeen(h.seen_days, t)}</span>
                      </span>
                    </a>
                  );
                })}
              </div>
            )
            : <EmptyState title={t("common.no_results")} hint={t("home.recall_empty_hint")} />)}
        </section>
      </div>
    </div>
  );
}
