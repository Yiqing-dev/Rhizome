// SPDX-License-Identifier: Apache-2.0
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import TopicGraph from "../components/TopicGraph";
import { EmptyState, Loading } from "../components/common";
import { communityColor, surface, tint } from "../components/colors";
import { GraphIcon } from "../components/icons";
import { useLoad } from "../hooks";
import { go, href } from "../router";

export default function MapPage() {
  const { t } = useTranslation();
  const [cands, setCands] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const map = useLoad(() => api.topicMap(cands), [cands]);
  const [form, setForm] = useState({ name: "", definition: "", examples: "", counter: "", parent: "" });
  const [busy, setBusy] = useState(false);
  const onHover = useCallback((id: number | null) => setHover(id), []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const split = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
      const r = await api.createTopic({ name: form.name, definition: form.definition || undefined, examples: split(form.examples),
        counter_examples: split(form.counter), parent: form.parent || undefined });
      go(`topic/${r.topic_id}`);
    } finally {
      setBusy(false);
    }
  }

  const nodes = map.data ? [...map.data.nodes].sort((a, b) => b.size - a.size) : [];
  const communities = new Set(nodes.map((n) => n.community).filter((c) => c !== null)).size;
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>{t("map.title")}</h1>
          <p className="sub">{t("map.sub")}</p>
        </div>
        <label className="check"><input type="checkbox" checked={cands} onChange={(e) => setCands(e.target.checked)} />
          {t("map.show_candidates")}</label>
      </div>
      <Loading error={map.error} loading={map.loading && !map.data} />
      {map.data && (nodes.length ? (
        <div className="map-layout">
          <section className="graph-card">
            <header>
              <GraphIcon />
              <b>{t("map.graph_title", { n: map.data.total_topics })}</b>
              <span className="grow" />
              <span className="muted small">{communities > 0 ? t("map.communities", { n: communities }) : ""}</span>
            </header>
            <TopicGraph map={map.data} onHover={onHover} />
            <div className="legend"><span className="muted">{t("map.legend")}</span></div>
          </section>
          <aside className="map-side">
            <section className="card">
              <header style={{ padding: "0.55rem 0.9rem", borderBottom: "1px solid var(--line)" }}><h3>{t("map.list_title")}</h3></header>
              <ul className="topic-list">
                {nodes.map((n) => (
                  <li key={n.id} style={{ "--cc": n.status === "candidate" ? tint(communityColor(n.community), surface(), 0.55) : communityColor(n.community),
                    background: hover === n.id ? "var(--accent-soft)" : undefined } as React.CSSProperties}>
                    <i /><a href={href(`topic/${n.id}`)}>{n.name}</a><span className="n">{n.size}</span>
                  </li>
                ))}
              </ul>
            </section>
          </aside>
        </div>
      ) : <EmptyState title={t("map.empty")} hint={t("map.empty_hint")} />)}
      <form className="panel" onSubmit={create}>
        <h2>{t("map.new_topic")}</h2>
        <p className="hint">{t("map.new_topic_hint")}</p>
        <div className="form-grid">
          <label>{t("map.name")}<input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>{t("map.parent")}<input value={form.parent} onChange={(e) => setForm({ ...form, parent: e.target.value })} /></label>
        </div>
        <label>{t("map.definition")}<input value={form.definition} onChange={(e) => setForm({ ...form, definition: e.target.value })} /></label>
        <div className="form-grid">
          <label>{t("map.examples")}<textarea rows={3} value={form.examples} onChange={(e) => setForm({ ...form, examples: e.target.value })} /></label>
          <label>{t("map.counter")}<textarea rows={3} value={form.counter} onChange={(e) => setForm({ ...form, counter: e.target.value })} /></label>
        </div>
        <div className="row"><button type="submit" className="primary" disabled={busy || !form.name.trim()}>{t("map.create")}</button></div>
      </form>
    </div>
  );
}
