// SPDX-License-Identifier: Apache-2.0
import { useTranslation } from "react-i18next";
import { href, useRoute } from "./router";
import Home from "./pages/Home";
import SearchPage from "./pages/Search";
import { lazy, Suspense } from "react";
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

export default function App() {
  const { t } = useTranslation();
  const route = useRoute();
  const [head, id] = route.path;
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
  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href={href("")}>{t("app.name")}</a>
        <nav>
          {NAV.map(([p, k]) => (
            <a key={p} href={href(p)} className={(head ?? "") === p ? "active" : ""}>{t(k)}</a>
          ))}
        </nav>
      </header>
      <main><Suspense fallback={<div className="muted">{t("common.loading")}</div>}>{page}</Suspense></main>
    </div>
  );
}
