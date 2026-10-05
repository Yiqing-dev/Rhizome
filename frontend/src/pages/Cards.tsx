// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type DueCard } from "../api";
import { EmptyState, Loading } from "../components/common";
import { CardIcon } from "../components/icons";
import { useBusy, useKeys, useLoad } from "../hooks";
import { parseUtc } from "../i18n";

/** FSRS review: space reveals, 1–4 grades. */
export default function CardsPage() {
  const { t } = useTranslation();
  const due = useLoad(() => api.cardsDue(), []);
  const [i, setI] = useState(0);
  const [shown, setShown] = useState(false);
  const [done, setDone] = useState(0);
  // FSRS learning steps: a card graded "Again" is due again in minutes; it waits here and comes
  // back into the deck when its time arrives instead of vanishing until the page is reopened
  const [waiting, setWaiting] = useState<{ card: DueCard; at: number }[]>([]);
  const [relearn, setRelearn] = useState<DueCard[]>([]);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 15000);
    return () => window.clearInterval(id);
  }, []);
  useEffect(() => {
    const ready = waiting.filter((w) => w.at <= now);
    if (ready.length) {
      setWaiting((w) => w.filter((x) => x.at > now));
      setRelearn((r) => [...r, ...ready.map((x) => x.card)]);
    }
  }, [now, waiting]);
  const [, guard] = useBusy();
  const cards = [...(due.data?.cards ?? []), ...relearn.filter((c) => !(due.data?.cards ?? []).some((d) => d.id === c.id))];
  const card = cards[i];

  async function grade(r: number) {
    if (!card || !shown) return;
    await guard(async () => {
      const res = await api.grade(card.id, r);
      const at = parseUtc(res.due).getTime();
      if (at - Date.now() < 30 * 60 * 1000) setWaiting((w) => [...w.filter((x) => x.card.id !== card.id), { card, at }]);
      setRelearn((rl) => rl.filter((c) => c.id !== card.id));
      setShown(false);
      setDone((d) => d + 1);
      if (i + 1 < cards.length) setI(i + 1);
      else { setI(0); due.reload(); }
    });
  }
  const nextWait = waiting.length ? Math.max(0, Math.ceil((Math.min(...waiting.map((w) => w.at)) - now) / 60000)) : null;

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
      {due.data && !card && waiting.length > 0 && (
        <EmptyState icon={<CardIcon />} title={t("cards.learning_wait", { n: waiting.length })} hint={t("cards.learning_hint", { min: nextWait ?? 0 })} />
      )}
      {due.data && !card && !waiting.length && <EmptyState icon={<CardIcon />} title={t("cards.done")} hint={t("cards.done_hint")} />}
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
