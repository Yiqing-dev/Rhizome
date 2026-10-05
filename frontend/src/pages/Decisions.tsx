// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, ApiError, type Decision } from "../api";
import { EmptyState, Loading } from "../components/common";
import { useLoad } from "../hooks";
import { fmtDate } from "../i18n";

/** Everything you decided (merges, renames, rejections, withdrawn exports, ...), newest first;
 * each can be undone, after which the library is rebuilt without it. */
export default function DecisionsPage() {
  const { t, i18n } = useTranslation();
  const [offset, setOffset] = useState(0);
  const list = useLoad(() => api.decisions(50, offset), [offset]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function undo(d: Decision) {
    if (!window.confirm(t("decisions.confirm", { id: d.id }))) return;
    try {
      const r = await api.revoke(d.id);
      setMsg({ ok: true, text: r.changed ? t("decisions.undone", { id: d.id }) : t("decisions.already", { id: d.id }) });
      list.reload();
    } catch (e) {
      setMsg({ ok: false, text: String((e as ApiError).detail ?? e) });
    }
  }

  const rows = list.data?.decisions ?? [];
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>{t("decisions.title")}</h1>
          <p className="sub">{t("decisions.hint")}</p>
        </div>
      </div>
      {msg && <div className={`notice ${msg.ok ? "ok" : "error"}`}>{msg.text}</div>}
      <Loading error={list.error} loading={list.loading && !list.data} />
      {list.data && !rows.length && <EmptyState title={t("decisions.empty")} />}
      {rows.length > 0 && (
        <table className="decisions">
          <thead><tr><th>#</th><th>{t("decisions.when")}</th><th>{t("decisions.what")}</th><th>{t("decisions.details")}</th><th /></tr></thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.id} className={d.revoked_at ? "revoked" : ""}>
                <td className="mono">{d.id}</td>
                <td className="small">{fmtDate(d.created_at, i18n.language)}</td>
                <td>{t(`decisions.op.${d.op}`, { defaultValue: d.op })}</td>
                <td className="mono small">{summary(d.payload)}</td>
                <td>{d.revoked_at
                  ? <span className="pill">{t("decisions.revoked")}</span>
                  : <button className="link danger" onClick={() => undo(d)}>{t("decisions.undo")}</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row">
        {offset > 0 && <button onClick={() => setOffset(Math.max(0, offset - 50))}>{t("common.prev")}</button>}
        {rows.length === 50 && <button onClick={() => setOffset(offset + 50)}>{t("common.next")}</button>}
      </div>
    </div>
  );
}

function summary(p: Record<string, unknown>): string {
  const keys = ["from", "into", "key", "name", "alias", "text", "work", "src", "type", "dst", "status", "a", "b"];
  return keys.filter((k) => p[k] !== undefined).map((k) => `${k}: ${String(p[k]).slice(0, 60)}`).join(" · ");
}
