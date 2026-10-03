// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { EmptyState, HitRow, Loading } from "../components/common";
import { SearchIcon } from "../components/icons";
import { useLoad } from "../hooks";
import { go } from "../router";

const TYPES = ["work", "dataset", "method", "idea", "claim", "topic"];
const ROLES = ["", "proposes", "uses", "produces", "evaluates", "supports", "contradicts"];

export default function SearchPage({ query }: { query: URLSearchParams }) {
  const { t } = useTranslation();
  const [form, setForm] = useState(() => Object.fromEntries(query.entries()) as Record<string, string>);
  const q = (query.get("q") ?? "").trim();
  const params = Object.fromEntries(query.entries());
  const browsing = !q && !!params.types;  // tiles on the home page: list everything of a type
  const res = useLoad(() => (q || browsing ? api.search(q, params) : Promise.resolve({ results: [] })), [query.toString()]);
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const types = (form.types ?? "").split(",").filter(Boolean);
  const toggleType = (ty: string) => set("types", (types.includes(ty) ? types.filter((x) => x !== ty) : [...types, ty]).join(","));
  const n = res.data?.results.length ?? 0;

  return (
    <div className="stack">
      <form className="filters" onSubmit={(e) => { e.preventDefault(); go("search", form); }}>
        <div className="row">
          <div className="searchbar grow" style={{ boxShadow: "none" }}>
            <input value={form.q ?? ""} onChange={(e) => set("q", e.target.value)} placeholder={t("search.placeholder")} autoFocus />
            <button type="submit" className="primary"><SearchIcon />{t("search.go")}</button>
          </div>
        </div>
        <div className="row wrap">
          {TYPES.map((ty) => (
            <label key={ty} className={`chip t-${ty} ${types.includes(ty) ? "on" : ""}`}>
              <input type="checkbox" checked={types.includes(ty)} onChange={() => toggleType(ty)} />
              {t(`type.${ty}`)}
            </label>
          ))}
        </div>
        <div className="row wrap">
          <input value={form.organism ?? ""} onChange={(e) => set("organism", e.target.value)} placeholder={t("search.organism")} />
          <input value={form.modality ?? ""} onChange={(e) => set("modality", e.target.value)} placeholder={t("search.modality")} />
          <input type="number" value={form.year_min ?? ""} onChange={(e) => set("year_min", e.target.value)} placeholder={t("search.year_min")} />
          <input type="number" value={form.year_max ?? ""} onChange={(e) => set("year_max", e.target.value)} placeholder={t("search.year_max")} />
          <select value={form.edge_type ?? ""} onChange={(e) => set("edge_type", e.target.value)}>
            {ROLES.map((r) => <option key={r} value={r}>{r ? t(`edge.${r}`) : t("search.any_role")}</option>)}
          </select>
        </div>
      </form>
      <Loading error={res.error} loading={res.loading} />
      {res.data && (q || browsing) && (
        <div className="row between">
          <span className="muted small">{t("search.results_count", { n })}</span>
        </div>
      )}
      {res.data && (n
        ? <ul className="hits">{res.data.results.map((h) => <HitRow key={h.id} h={h} />)}</ul>
        : q || browsing ? <EmptyState title={t("common.no_results")} hint={t("search.empty_hint")} /> : null)}
    </div>
  );
}
