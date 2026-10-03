// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { HitRow, Loading } from "../components/common";
import { useLoad } from "../hooks";
import { go } from "../router";

const TYPES = ["work", "dataset", "method", "idea", "claim", "topic"];
const ROLES = ["", "proposes", "uses", "produces", "evaluates", "supports", "contradicts"];

export default function SearchPage({ query }: { query: URLSearchParams }) {
  const { t } = useTranslation();
  const [form, setForm] = useState(() => Object.fromEntries(query.entries()) as Record<string, string>);
  const q = query.get("q") ?? "";
  const params = Object.fromEntries(query.entries());
  const res = useLoad(() => (q ? api.search(q, params) : Promise.resolve({ results: [] })), [query.toString()]);
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const types = (form.types ?? "").split(",").filter(Boolean);

  return (
    <div className="stack">
      <form className="filters" onSubmit={(e) => { e.preventDefault(); go("search", form); }}>
        <input value={form.q ?? ""} onChange={(e) => set("q", e.target.value)} placeholder={t("search.placeholder")} />
        <div className="row wrap">
          {TYPES.map((ty) => (
            <label key={ty} className="check">
              <input type="checkbox" checked={types.includes(ty)} onChange={(e) =>
                set("types", (e.target.checked ? [...types, ty] : types.filter((x) => x !== ty)).join(","))} />
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
          <button type="submit">{t("search.go")}</button>
        </div>
      </form>
      <Loading error={res.error} loading={res.loading} />
      {res.data && (res.data.results.length
        ? <ul className="hits">{res.data.results.map((h) => <HitRow key={h.id} h={h} />)}</ul>
        : q ? <p className="muted">{t("common.no_results")}</p> : null)}
    </div>
  );
}
