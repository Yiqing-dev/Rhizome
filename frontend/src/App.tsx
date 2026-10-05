// SPDX-License-Identifier: Apache-2.0
import { lazy, Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errorText } from "./api";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { showToast, Toasts } from "./components/Toast";
import { Logo, SearchIcon } from "./components/icons";
import { useJob } from "./hooks";
import { backendLang, setLanguage } from "./i18n";
import { go, href, useRoute } from "./router";
import Home from "./pages/Home";
import SearchPage, { searchKey } from "./pages/Search";
import TopicPage from "./pages/Topic";
import ReviewPage from "./pages/Review";
import CardsPage from "./pages/Cards";
import DigestPage from "./pages/Digest";
import SettingsPage from "./pages/Settings";
import DecisionsPage from "./pages/Decisions";

// graph libraries (Cytoscape, sigma) load only when a graph view is opened
const EntityPage = lazy(() => import("./pages/Entity"));
const MapPage = lazy(() => import("./pages/Map"));

const NAV = [
  ["", "nav.home"], ["search", "nav.search"], ["map", "nav.map"], ["review", "nav.review"],
  ["cards", "nav.cards"], ["digest", "nav.digest"], ["settings", "nav.settings"],
] as const;

function TopbarSearch() {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  return (
    <form className="topbar-search" role="search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) { go("search", { q }); setQ(""); } }}>
      <SearchIcon />
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("search.placeholder")} aria-label={t("nav.search")} />
    </form>
  );
}

function LangToggle() {
  const { t, i18n } = useTranslation();
  const cur = i18n.language.startsWith("zh") ? "zh-CN" : "en";
  const pick = (l: "en" | "zh-CN") => {
    setLanguage(l);
    api.patchSettings({ language: backendLang(l) }).catch(() => undefined);
  };
  return (
    <div className="lang-toggle" role="group">
      <button type="button" className={cur === "en" ? "active" : ""} onClick={() => pick("en")} lang="en">{t("lang.short_en")}</button>
      <button type="button" className={cur === "zh-CN" ? "active" : ""} onClick={() => pick("zh-CN")} lang="zh-CN">{t("lang.short_zh")}</button>
    </div>
  );
}

/** A model named in settings.json that this program cannot load (e.g. bge-m3 chosen from a pip
 * install, while the desktop app has only the built-in models): every ingest and search fails,
 * so say it on every page and offer the fix. */
function ModelBanner() {
  const { t } = useTranslation();
  const [problems, setProblems] = useState<{ kind: string; model: string }[]>([]);
  const [stale, setStale] = useState<{ model: string | null; current: string | null } | null>(null);
  const [done, setDone] = useState(false);
  const rebuild = useJob("rebuild", () => setStale(null));
  useEffect(() => {
    api.system().then((s) => {
      setProblems(s.model_problems ?? []);
      setStale(s.index?.stale ? s.index : null);
    }).catch(() => undefined);
  }, []);
  if (!problems.length && stale) {
    return (
      <div className="notice warn banner">
        <div className="row wrap">
          <span className="grow">{t("models.index_stale", { index: stale.model, current: stale.current })}</span>
          <button className="primary" onClick={rebuild.start} disabled={rebuild.running}>
            {rebuild.running ? t("jobs.running") : t("settings.rebuild")}</button>
        </div>
      </div>
    );
  }
  if (!problems.length) return null;
  const useBuiltin = async () => {
    await api.patchSettings({ embedder: null, reranker: null, nli: null });
    await api.runJob("rebuild");
    setDone(true);
  };
  return (
    <div className={`notice banner ${done ? "ok" : "error"}`}>
      {done ? t("models.switched") : (
        <div className="row wrap">
          <span className="grow">{t("models.unavailable", { models: problems.map((p) => p.model).join(", ") })}</span>
          <button className="primary" onClick={useBuiltin}>{t("models.use_builtin")}</button>
        </div>
      )}
    </div>
  );
}

/** While a rebuild runs, changes are refused (503): say so on every page and clear when done. */
function JobBanner() {
  const { t } = useTranslation();
  const [active, setActive] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const poll = () => api.activeJobs()
      .then((r) => {
        if (!alive) return;
        const run = r.jobs.find((j) => j.status === "running" && j.kind === "rebuild");
        setActive(run ? run.kind : null);
        timer = window.setTimeout(poll, run ? 3000 : 15000);
      })
      .catch(() => { if (alive) timer = window.setTimeout(poll, 15000); });
    poll();
    return () => { alive = false; window.clearTimeout(timer); };
  }, []);
  if (!active) return null;
  return <div className="notice warn banner">{t("jobs.rebuilding_banner")}</div>;
}

/** Read-only (a snapshot) or moved library: say so on every page; offer the restart. */
function ModeBanner() {
  const { t } = useTranslation();
  const [h, setH] = useState<{ read_only: boolean; moved_to?: string | null } | null>(null);
  useEffect(() => {
    api.health().then(setH).catch(() => undefined);
    const id = window.setInterval(() => api.health().then(setH).catch(() => undefined), 30000);
    return () => window.clearInterval(id);
  }, []);
  if (!h?.read_only) return null;
  if (h.moved_to) {
    return (
      <div className="notice warn banner row wrap">
        <span className="grow">{t("mode.moved", { path: h.moved_to })}</span>
        <button className="primary" onClick={() => api.restart().catch((e) => showToast(errorText(e, t)))}>{t("mode.restart")}</button>
      </div>
    );
  }
  return <div className="notice warn banner">{t("mode.read_only")}</div>;
}

export default function App() {
  const { t, i18n } = useTranslation();
  useEffect(() => {
    // anything that fails without its own handling still reaches the user
    const onRejection = (e: PromiseRejectionEvent) => showToast(errorText(e.reason, t));
    const onError = (e: ErrorEvent) => showToast(e.message);
    window.addEventListener("unhandledrejection", onRejection);
    window.addEventListener("error", onError);
    return () => {
      window.removeEventListener("unhandledrejection", onRejection);
      window.removeEventListener("error", onError);
    };
  }, [t]);
  const route = useRoute();
  const [head, id] = route.path;
  const [queue, setQueue] = useState<number>(0);
  useEffect(() => {
    document.documentElement.lang = i18n.language;
  }, [i18n.language]);
  useEffect(() => {
    api.stats().then((s) => setQueue(s.review_queue + 0)).catch(() => undefined);
  }, [route.path.join("/")]);

  let page;
  switch (head) {
    case "search": page = <SearchPage key={searchKey(route.query)} query={route.query} />; break;
    case "entity": page = <EntityPage id={Number(id)} />; break;
    case "topic": page = <TopicPage id={Number(id)} />; break;
    case "map": page = <MapPage />; break;
    case "review": page = <ReviewPage />; break;
    case "cards": page = <CardsPage />; break;
    case "digest": page = <DigestPage />; break;
    case "settings": page = <SettingsPage />; break;
    case "decisions": page = <DecisionsPage />; break;
    default: page = <Home />;
  }
  const isHome = !head;
  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href={href("")}><Logo />{t("app.name")}</a>
          <nav>
            {NAV.map(([p, k]) => (
              <a key={p} href={href(p)} className={(head ?? "") === p ? "active" : ""}>
                {t(k)}
                {p === "review" && queue > 0 ? <span className="nav-count">{queue}</span> : null}
              </a>
            ))}
          </nav>
          <span className="spacer" />
          {!isHome && <TopbarSearch />}
          <LangToggle />
        </div>
      </header>
      <main>
        <ModeBanner />
        <ModelBanner />
        <JobBanner />
        <ErrorBoundary resetKey={route.path.join("/")}
          labels={{ title: t("common.page_error"), home: t("nav.home"), copy: t("common.copy_details") }}>
          <Suspense fallback={<div className="muted">{t("common.loading")}</div>}>{page}</Suspense>
        </ErrorBoundary>
        <Toasts />
      </main>
    </div>
  );
}
