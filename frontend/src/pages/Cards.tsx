// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { EmptyState, Loading } from "../components/common";
import { CardIcon } from "../components/icons";
import { useKeys, useLoad } from "../hooks";

/** FSRS review: space reveals, 1–4 grades. */
export default function CardsPage() {
  const { t } = useTranslation();
  const due = useLoad(() => api.cardsDue(), []);
  const [i, setI] = useState(0);
  const [shown, setShown] = useState(false);
  const [done, setDone] = useState(0);
  const cards = due.data?.cards ?? [];
  const card = cards[i];

  async function grade(r: number) {
    if (!card || !shown) return;
    await api.grade(card.id, r);
    setShown(false);
    setDone((d) => d + 1);
    if (i + 1 < cards.length) setI(i + 1);
    else { setI(0); due.reload(); }
  }

  async function suspend(entity: boolean) {
    if (!card) return;
    if (entity && !window.confirm(t("cards.never_confirm", { name: card.entity_name ?? "" }))) return;
    await api.suspendCard(card.id, entity);
    setShown(false);
    if (i + 1 < cards.length) setI(i + 1);
    else { setI(0); due.reload(); }
  }

  useKeys((e) => {
    if (e.key === " ") { e.preventDefault(); setShown(true); }
    else if (["1", "2", "3", "4"].includes(e.key)) grade(Number(e.key));
    else if (e.key === "s") suspend(false);
  }, [card, shown, i, cards.length]);
  // generated cards name the asset in the question's answer: keep the header blank until revealed
  const header = card && (shown || card.origin === "rxf" || card.origin === "user") ? card.entity_name : "";

  return (
    <div className="stack narrow" style={{ margin: "0 auto" }}>
      <div className="page-head">
        <div>
          <h1>{t("cards.title")}</h1>
          <p className="sub">{t("cards.sub")} {t("cards.sub_suspend")}</p>
        </div>
        {cards.length > 0 && <span className="muted small">{t("cards.progress", { done, total: done + cards.length - i })}</span>}
      </div>
      <Loading error={due.error} loading={due.loading && !due.data} />
      {due.data && !card && <EmptyState icon={<CardIcon />} title={t("cards.done")} hint={t("cards.done_hint")} />}
      {card && (
        <>
          <div className="progress"><i style={{ "--w": `${(100 * i) / cards.length}%` } as React.CSSProperties} /></div>
          <div className="flashcard">
            <div className="meta">
              <span>{header}</span>
              <span>{card.new ? t("cards.new") : t(`cards.origin.${card.origin}`, { defaultValue: "" })}</span>
            </div>
            <p className="q">{card.q}</p>
            {shown ? <p className="a">{card.a}</p> : (
              <div className="row"><button className="primary" onClick={() => setShown(true)}><kbd>␣</kbd> {t("cards.show")}</button></div>
            )}
            {shown && (
              <div className="ratings">
                {[1, 2, 3, 4].map((r) => <button key={r} className={`r${r}`} onClick={() => grade(r)}><kbd>{r}</kbd> {t(`cards.rating.${r}`)}</button>)}
              </div>
            )}
            <div className="row wrap small" style={{ marginTop: "0.6rem" }}>
              <button className="ghost" onClick={() => suspend(false)}><kbd>{"s"}</kbd> {t("cards.suspend")}</button>
              <button className="ghost" onClick={() => suspend(true)}>{t("cards.never")}</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
