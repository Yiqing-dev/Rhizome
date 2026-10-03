// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type ReviewItem } from "../api";
import { EmptyState, Loading, TypeBadge } from "../components/common";
import { CheckIcon } from "../components/icons";
import { useKeys, useLoad } from "../hooks";
import { href } from "../router";

const KINDS = ["", "merge", "topic_relation", "contradiction", "retro_tag", "synthesis"];

function Side({ label, side }: { label: string; side?: ReviewItem["context"][string] }) {
  const { t } = useTranslation();
  if (!side) return null;
  return (
    <div className="side">
      <div className="lbl">{label}</div>
      <div className="name"><TypeBadge type={side.type} /> <a href={href(`${side.type === "topic" ? "topic" : "entity"}/${side.id}`)}>{side.name}</a></div>
      {side.definition && <p className="small muted">{side.definition}</p>}
      {side.aliases.length > 1 && <div className="small muted">{t("asset.aliases")}: {side.aliases.join(" · ")}</div>}
      {side.connections.length > 0 && (
        <ul>{side.connections.map((c, i) => (
          <li key={i}>{c.direction === "out" ? "→" : "←"} <span className="muted">{t(`edge.${c.type}`)}</span> {c.name}</li>))}</ul>
      )}
    </div>
  );
}

/** Batch, keyboard-only: j/k move, 1–5 pick an action, s skip. */
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

  const label = (it: ReviewItem) => (it.payload.a_name ?? it.payload.name ?? it.payload.new_text ?? "") as string;
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>{t("review.title")}</h1>
          <p className="sub">{t("review.keys")}</p>
        </div>
        <div className="row">
          <select value={kind} onChange={(e) => { setKind(e.target.value); setCur(0); }}>
            {KINDS.map((k) => <option key={k} value={k}>{k ? t(`review.kind.${k}`) : t("review.all")}</option>)}
          </select>
          <span className="muted small">{t("review.total", { n: q.data?.total ?? 0 })}</span>
        </div>
      </div>
      <Loading error={q.error} loading={q.loading && !q.data} />
      {q.data && !items.length && <EmptyState icon={<CheckIcon />} title={t("review.empty")} hint={t("review.empty_hint")} />}
      {items.length > 0 && (
        <div className="review-layout">
          <ol className="queue">
            {items.map((it, i) => (
              <li key={it.id} className={it === item ? "current" : ""} onClick={() => setCur(i)}>
                <span className="kind">{t(`review.kind.${it.kind}`)}</span>
                <span>{label(it).slice(0, 80)}</span>
              </li>
            ))}
          </ol>
          {item && (
            <div className="panel">
              <span className="eyebrow">{t(`review.kind.${item.kind}`)}</span>
              <div className="question">{String(t(`review.question.${item.kind}`, { ...item.payload }))}</div>
              <div className="sides">
                <Side label={t("review.side_a")} side={item.context.a ?? item.context.key ?? item.context.new_claim} />
                <Side label={t("review.side_b")} side={item.context.b ?? item.context.claim ?? item.context.topic} />
              </div>
              {item.kind === "contradiction" && (
                <p className="small"><span className="muted">{t("review.new_claim")}:</span> {item.payload.new_text}
                  {item.payload.evidence ? <> · <span className="evidence">{item.payload.evidence}</span></> : null}</p>
              )}
              {item.kind === "synthesis" && (
                <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("review.synthesis_note")} />
              )}
              <div className="actions">
                {item.actions.map((a, i) => (
                  <button key={a} className={i === 0 ? "primary" : ""} onClick={() => act(a)}><kbd>{i + 1}</kbd> {t(`review.action.${a}`)}</button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
