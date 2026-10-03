// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type ReviewItem } from "../api";
import { Loading } from "../components/common";
import { useKeys, useLoad } from "../hooks";
import { href } from "../router";

const KINDS = ["", "merge", "topic_relation", "contradiction", "retro_tag", "synthesis"];

function Side({ label, side }: { label: string; side?: ReviewItem["context"][string] }) {
  const { t } = useTranslation();
  if (!side) return null;
  return (
    <div className="side">
      <div className="muted small">{label}</div>
      <a href={href(`${side.type === "topic" ? "topic" : "entity"}/${side.id}`)}><strong>{side.name}</strong></a>
      {side.definition && <p className="small">{side.definition}</p>}
      {side.aliases.length > 1 && <div className="small muted">{t("asset.aliases")}: {side.aliases.join(" · ")}</div>}
      <ul className="small">{side.connections.map((c, i) => (
        <li key={i}>{c.direction === "out" ? "→" : "←"} {t(`edge.${c.type}`)}: {c.name}</li>))}</ul>
    </div>
  );
}

/** Batch, keyboard-only: j/k move, 1-5 pick an action, s skip. */
export default function ReviewPage() {
  const { t } = useTranslation();
  const [kind, setKind] = useState("");
  const [cur, setCur] = useState(0);
  const [note, setNote] = useState("");
  const q = useLoad(() => api.review(kind || undefined), [kind]);
  const items = q.data?.items ?? [];
  const item = items[Math.min(cur, Math.max(0, items.length - 1))];

  async function act(action: string) {
    if (!item) return;
    await api.resolve(item.id, action, note || undefined);
    setNote("");
    q.reload();
  }

  useKeys((e) => {
    if (e.key === "j") setCur((c) => Math.min(c + 1, items.length - 1));
    else if (e.key === "k") setCur((c) => Math.max(c - 1, 0));
    else if (e.key === "s") act("skip");
    else if (/^[1-9]$/.test(e.key) && item) {
      const a = item.actions[Number(e.key) - 1];
      if (a) act(a);
    }
  }, [items, item, note]);

  return (
    <div className="stack">
      <div className="row">
        <h1>{t("review.title")}</h1>
        <select value={kind} onChange={(e) => { setKind(e.target.value); setCur(0); }}>
          {KINDS.map((k) => <option key={k} value={k}>{k ? t(`review.kind.${k}`) : t("review.all")}</option>)}
        </select>
        <span className="muted">{t("review.total", { n: q.data?.total ?? 0 })}</span>
      </div>
      <p className="muted small">{t("review.keys")}</p>
      <Loading error={q.error} loading={q.loading && !q.data} />
      {q.data && !items.length && <p>{t("review.empty")}</p>}
      <div className="review-layout">
        <ol className="queue">
          {items.map((it, i) => (
            <li key={it.id} className={it === item ? "current" : ""} onClick={() => setCur(i)}>
              <span className="pill">{t(`review.kind.${it.kind}`)}</span>{" "}
              {(it.payload.a_name ?? it.payload.name ?? it.payload.new_text ?? "").slice(0, 60)}
            </li>
          ))}
        </ol>
        {item && (
          <div className="panel">
            <h2>{String(t(`review.question.${item.kind}`, { ...item.payload }))}</h2>
            <div className="sides">
              <Side label="A" side={item.context.a ?? item.context.key ?? item.context.new_claim} />
              <Side label="B" side={item.context.b ?? item.context.claim ?? item.context.topic} />
            </div>
            {item.kind === "contradiction" && (
              <p className="small">{t("review.new_claim")}: {item.payload.new_text} <span className="evidence">· {item.payload.evidence}</span></p>
            )}
            {item.kind === "synthesis" && (
              <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("review.synthesis_note")} />
            )}
            <div className="row wrap">
              {item.actions.map((a, i) => (
                <button key={a} onClick={() => act(a)}><kbd>{i + 1}</kbd> {t(`review.action.${a}`)}</button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
