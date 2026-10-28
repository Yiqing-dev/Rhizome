// SPDX-License-Identifier: Apache-2.0
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type OrganiseRun } from "../api";
import TopicGraph from "../components/TopicGraph";
import { EmptyState, Loading } from "../components/common";
import { communityColor, cssVar, tint } from "../components/colors";
import { GraphIcon } from "../components/icons";
import { useJob, useLoad } from "../hooks";
import { go, href } from "../router";

export default function MapPage() {
  const { t } = useTranslation();
  const [cands, setCands] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const [focus, setFocus] = useState<number | null>(null);
  const [filter, setFilter] = useState("");
  const map = useLoad(() => api.topicMap(cands), [cands]);
  const [form, setForm] = useState({ name: "", definition: "", examples: "", counter: "", parent: "" });
  const [busy, setBusy] = useState(false);
  const onHover = useCallback((id: number | null) => setHover(id), []);
  const [run, setRun] = useState<OrganiseRun | null>(null);
  const organise = useJob("organise_tree", (j) => {
    setRun(j.status === "failed" ? { error: j.error ?? "" } : (j.result as OrganiseRun));
    map.reload();
  });
  const last = run ?? map.data?.ai?.last_run ?? null;

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
  const members = new Map<number, number>();
  nodes.forEach((n) => { if (n.community !== null) members.set(n.community, (members.get(n.community) ?? 0) + 1); });
  const communities = [...members.values()].filter((n) => n >= 2).length;
  const bg = cssVar("--bg", "#ffffff"), ink = cssVar("--ink", "#1c1f1a");
  const colourOf = (n: (typeof nodes)[number]) =>
    n.community !== null && (members.get(n.community) ?? 0) >= 2 ? communityColor(n.community) : tint(ink, bg, 0.6);
  const needle = filter.trim().toLowerCase();
  const shown = needle ? nodes.filter((n) => n.name.toLowerCase().includes(needle)) : nodes;
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>{t("map.title")}</h1>
          <p className="sub">{t("map.sub")}</p>
        </div>
        <div className="row wrap">
          {map.data?.ai?.enabled && (
            <button onClick={() => { setRun(null); organise.start(); }} disabled={organise.running} title={t("map.organise_hint")}>
              {organise.running ? t("map.organising") : t("map.organise")}</button>
          )}
          <label className="check"><input type="checkbox" checked={cands} onChange={(e) => setCands(e.target.checked)} />
            {t("map.show_candidates")}</label>
        </div>
      </div>
      {last && !organise.running && (
        <div className={`notice ${last.error || last.stopped || last.skipped ? "warn" : ""} small`}>
          {last.error || last.stopped || last.skipped ? t("topic.organise_failed", { error: last.error || last.stopped || last.skipped }) : (
            <>
              {last.summary && <p>{last.summary}</p>}
              {(last.queued ?? 0) > 0
                ? <a href={href("review?kind=suggestion")}>{t("topic.organise_queued", { n: last.queued })} →</a>
                : <span className="muted">{t("topic.organise_nothing")}</span>}
            </>
          )}
        </div>
      )}
      <Loading error={map.error} loading={map.loading && !map.data} skeleton="map" />
      {map.data && (nodes.length ? (
        <div className="map-layout">
          <section className="graph-card">
            <header>
              <GraphIcon />
              <b>{t("map.graph_title", { n: map.data.total_topics })}</b>
              <span className="grow" />
              <span className="muted small">{communities > 0 ? t("map.communities", { n: communities }) : t("map.no_communities")}</span>
            </header>
            <TopicGraph map={map.data} onHover={onHover} focus={focus} />
            <div className="legend"><span className="muted">{t("map.legend")}</span></div>
          </section>
          <aside className="map-side">
            <section className="card">
              <header className="topic-list-head"><h3>{t("map.list_title")}</h3>
                <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={t("map.find")} aria-label={t("map.find")} /></header>
              <ul className="topic-list" onMouseLeave={() => setFocus(null)}>
                {shown.map((n) => (
                  <li key={n.id} onMouseEnter={() => setFocus(n.id)}
                    style={{ "--cc": n.status === "candidate" ? tint(colourOf(n), bg, 0.55) : colourOf(n),
                    background: hover === n.id || focus === n.id ? "var(--accent-soft)" : undefined } as React.CSSProperties}>
                    <i /><a href={href(`topic/${n.id}`)}>{n.name}</a><span className="n">{n.size}</span>
                  </li>
                ))}
                {needle && !shown.length && <li className="muted small">{t("common.no_results")}</li>}
              </ul>
            </section>
          </aside>
        </div>
      ) : <EmptyState title={t("map.empty")} hint={t("map.empty_hint")} />)}
      <details className="panel disclosure">
        <summary><h2>{t("map.new_topic")}</h2><span className="hint">{t("map.new_topic_hint")}</span></summary>
        <form className="stack-sm" onSubmit={create}>
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
      </details>
    </div>
  );
}
