// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Loading } from "../components/common";
import { useKeys, useLoad } from "../hooks";

/** FSRS review: space reveals, 1-4 grades. */
export default function CardsPage() {
  const { t } = useTranslation();
  const due = useLoad(() => api.cardsDue(), []);
  const [i, setI] = useState(0);
  const [shown, setShown] = useState(false);
  const cards = due.data?.cards ?? [];
  const card = cards[i];

  async function grade(r: number) {
    if (!card || !shown) return;
    await api.grade(card.id, r);
    setShown(false);
    if (i + 1 < cards.length) setI(i + 1);
    else { setI(0); due.reload(); }
  }

  useKeys((e) => {
    if (e.key === " ") { e.preventDefault(); setShown(true); }
    else if (["1", "2", "3", "4"].includes(e.key)) grade(Number(e.key));
  }, [card, shown, i, cards.length]);

  return (
    <div className="stack narrow">
      <h1>{t("cards.title")}</h1>
      <Loading error={due.error} loading={due.loading && !due.data} />
      {due.data && !card && <p>{t("cards.done")}</p>}
      {card && (
        <div className="flashcard">
          <div className="muted small">{card.entity_name} · {i + 1}/{cards.length}{card.new ? ` · ${t("cards.new")}` : ""}</div>
          <p className="q">{card.q}</p>
          {shown ? <p className="a">{card.a}</p> : <button onClick={() => setShown(true)}><kbd>␣</kbd> {t("cards.show")}</button>}
          {shown && (
            <div className="row">
              {[1, 2, 3, 4].map((r) => <button key={r} onClick={() => grade(r)}><kbd>{r}</kbd> {t(`cards.rating.${r}`)}</button>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
