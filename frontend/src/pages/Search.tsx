// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errorText, type Hit } from "../api";
import { EmptyState, HitRow, Loading } from "../components/common";
import { SearchIcon } from "../components/icons";
import { go } from "../router";

const TYPES = ["work", "dataset", "method", "idea", "claim", "topic"];
const ROLES = ["", "proposes", "uses", "produces", "evaluates", "supports", "contradicts"];
const PAGE = 20;
const ADVANCED = ["organism", "modality", "year_min", "year_max", "edge_type"];

/** The query without paging: the page is keyed on it (App.tsx), so a top-bar search remounts the
 * form with the new values, while "load more" only changes the offset. */
export function searchKey(query: URLSearchParams): string {
  const p = new URLSearchParams(query);
  p.delete("offset");
  return p.toString();
}

export default function SearchPage({ query }: { query: URLSearchParams }) {
  const { t } = useTranslation();
  const [form, setForm] = useState(() => {
    const f = Object.fromEntries(query.entries()) as Record<string, string>;
    delete f.offset;
    return f;
  });
  const q = (query.get("q") ?? "").trim();
  const params = Object.fromEntries(query.entries());
  const offset = Number(params.offset ?? 0) || 0;
  const browsing = !q && !!params.types;  // tiles on the home page: list everything of a type
  const [items, setItems] = useState<Hit[]>([]);
  const [page, setPage] = useState<{ has_more?: boolean; total?: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!(q || browsing)) { setItems([]); setPage(null); return; }
    let live = true;
    setLoading(true);
    api.search(q, { ...params, limit: PAGE, offset })
      .then((r) => {
        if (!live) return;
        setItems((prev) => (offset > 0 ? [...prev, ...r.results] : r.results));
        setPage({ has_more: r.has_more, total: r.total });
        setError(null);
      })
      .catch((e) => live && setError(errorText(e, t)))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [query.toString()]);  // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const types = (form.types ?? "").split(",").filter(Boolean);
  const toggleType = (ty: string) => set("types", (types.includes(ty) ? types.filter((x) => x !== ty) : [...types, ty]).join(","));
  const n = items.length;
  const countLabel = page?.total !== undefined
    ? t("search.results_of", { n, total: page.total })
    : page?.has_more ? t("search.results_more", { n }) : t("search.results_count", { n });

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
        <details className="disclosure inline" open={ADVANCED.some((k) => !!form[k])}>
          <summary>{t("search.more_filters")}</summary>
        <div className="row wrap">
          <input value={form.organism ?? ""} onChange={(e) => set("organism", e.target.value)} placeholder={t("search.organism")} />
          <input value={form.modality ?? ""} onChange={(e) => set("modality", e.target.value)} placeholder={t("search.modality")} />
          <input type="number" value={form.year_min ?? ""} onChange={(e) => set("year_min", e.target.value)} placeholder={t("search.year_min")} />
          <input type="number" value={form.year_max ?? ""} onChange={(e) => set("year_max", e.target.value)} placeholder={t("search.year_max")} />
          <select value={form.edge_type ?? ""} onChange={(e) => set("edge_type", e.target.value)}>
            {ROLES.map((r) => <option key={r} value={r}>{r ? t(`edge.${r}`) : t("search.any_role")}</option>)}
          </select>
        </div>
        </details>
      </form>
      <Loading error={error} loading={loading && !items.length} />
      {page && (q || browsing) && (
        <div className="row between">
          <span className="muted small">{countLabel}</span>
        </div>
      )}
      {page && (n
        ? <ul className="hits">{items.map((h) => <HitRow key={h.id} h={h} />)}</ul>
        : q || browsing ? <EmptyState title={t("common.no_results")} hint={t("search.empty_hint")} /> : null)}
      {page?.has_more && (
        <div className="row">
          <button disabled={loading} onClick={() => go("search", { ...params, offset: String(n) })}>{t("search.load_more")}</button>
        </div>
      )}
    </div>
  );
}
