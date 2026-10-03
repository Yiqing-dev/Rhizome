// SPDX-License-Identifier: Apache-2.0
import { lazy, Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "./api";
import { Logo, SearchIcon } from "./components/icons";
import { backendLang, setLanguage } from "./i18n";
import { go, href, useRoute } from "./router";
import Home from "./pages/Home";
import SearchPage from "./pages/Search";
import TopicPage from "./pages/Topic";
import ReviewPage from "./pages/Review";
import CardsPage from "./pages/Cards";
import DigestPage from "./pages/Digest";
import SettingsPage from "./pages/Settings";

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

export default function App() {
  const { t, i18n } = useTranslation();
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
    case "search": page = <SearchPage query={route.query} />; break;
    case "entity": page = <EntityPage id={Number(id)} />; break;
    case "topic": page = <TopicPage id={Number(id)} />; break;
    case "map": page = <MapPage />; break;
    case "review": page = <ReviewPage />; break;
    case "cards": page = <CardsPage />; break;
    case "digest": page = <DigestPage />; break;
    case "settings": page = <SettingsPage />; break;
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
        <Suspense fallback={<div className="muted">{t("common.loading")}</div>}>{page}</Suspense>
      </main>
    </div>
  );
}
