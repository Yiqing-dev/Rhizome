// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import TopicGraph from "../components/TopicGraph";
import { Loading } from "../components/common";
import { useLoad } from "../hooks";
import { go } from "../router";

export default function MapPage() {
  const { t } = useTranslation();
  const [cands, setCands] = useState(false);
  const map = useLoad(() => api.topicMap(cands), [cands]);
  const [form, setForm] = useState({ name: "", definition: "", examples: "", counter: "", parent: "" });
  const [msg, setMsg] = useState("");

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const split = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
    const r = await api.createTopic({ name: form.name, definition: form.definition, examples: split(form.examples),
      counter_examples: split(form.counter), parent: form.parent || undefined });
    setMsg(t("map.created"));
    go(`topic/${r.topic_id}`);
  }

  return (
    <div className="stack">
      <div className="row">
        <h1>{t("map.title")}</h1>
        <label className="check"><input type="checkbox" checked={cands} onChange={(e) => setCands(e.target.checked)} />
          {t("map.show_candidates")}</label>
      </div>
      <Loading error={map.error} loading={map.loading && !map.data} />
      {map.data && (map.data.nodes.length ? <TopicGraph map={map.data} /> : <p className="muted">{t("map.empty")}</p>)}
      <form className="panel" onSubmit={create}>
        <h2>{t("map.new_topic")}</h2>
        <p className="muted">{t("map.new_topic_hint")}</p>
        <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={t("map.name")} />
        <input value={form.definition} onChange={(e) => setForm({ ...form, definition: e.target.value })} placeholder={t("map.definition")} />
        <textarea rows={3} value={form.examples} onChange={(e) => setForm({ ...form, examples: e.target.value })} placeholder={t("map.examples")} />
        <textarea rows={2} value={form.counter} onChange={(e) => setForm({ ...form, counter: e.target.value })} placeholder={t("map.counter")} />
        <input value={form.parent} onChange={(e) => setForm({ ...form, parent: e.target.value })} placeholder={t("map.parent")} />
        <button type="submit">{t("map.create")}</button>
        {msg && <span className="ok">{msg}</span>}
      </form>
    </div>
  );
}
