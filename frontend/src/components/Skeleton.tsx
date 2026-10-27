// SPDX-License-Identifier: Apache-2.0
// Loading placeholders shaped like the content they stand in for, so a page keeps its layout while
// the first request is in flight. Every shape is plain boxes: no text, nothing to translate.
import { useTranslation } from "react-i18next";

export type SkeletonKind = "list" | "tiles" | "entity" | "topic" | "review" | "map" | "table" | "panels" | "flashcard" | "digest" | "page";

/** One grey bar. `w` is any CSS width; `h` a CSS height (defaults to a line of text). */
export function Bar({ w, h, round }: { w?: string | number; h?: string | number; round?: boolean }) {
  return <span className={`sk ${round ? "round" : ""}`} style={{ width: w, height: h }} aria-hidden="true" />;
}

const Lines = ({ n = 3, last = "55%" }: { n?: number; last?: string }) => (
  <>{Array.from({ length: n }, (_, i) => <Bar key={i} w={i === n - 1 ? last : "100%"} />)}</>
);

const Hits = ({ n }: { n: number }) => (
  <ul className="hits sk-hits">
    {Array.from({ length: n }, (_, i) => (
      <li className="hit" key={i}>
        <div className="row"><Bar w="3.6rem" h="1.1rem" round /><Bar w={`${34 + ((i * 17) % 30)}%`} h="1rem" /></div>
        <Bar w={`${48 + ((i * 23) % 22)}%`} h="0.75rem" />
        <Bar w="22%" h="0.7rem" />
      </li>
    ))}
  </ul>
);

const Head = () => (
  <header className="entity-head">
    <div className="row"><Bar w="3.6rem" h="1.1rem" round /><Bar w="4.2rem" h="1.1rem" round /></div>
    <Bar w="min(60%, 28ch)" h="2rem" />
    <div className="row wrap"><Bar w="3rem" h="0.8rem" /><Bar w="9rem" h="0.8rem" /><Bar w="5rem" h="0.8rem" /></div>
  </header>
);

const Panel = ({ title = "9rem", lines = 2 }: { title?: string; lines?: number }) => (
  <section className="panel">
    <Bar w={title} h="1.15rem" />
    {Array.from({ length: lines }, (_, i) => (
      <div className="row" key={i}><Bar w="7rem" h="0.85rem" /><Bar w="min(60%, 22rem)" h="2rem" /></div>
    ))}
  </section>
);

const Table = ({ rows = 7, cols = 4 }: { rows?: number; cols?: number }) => (
  <table className="decisions sk-table">
    <thead><tr>{Array.from({ length: cols }, (_, c) => <th key={c}><Bar w={c === 0 ? "1.5rem" : "5rem"} h="0.75rem" /></th>)}</tr></thead>
    <tbody>
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r}>{Array.from({ length: cols }, (_, c) => (
          <td key={c}><Bar w={c === 0 ? "1.5rem" : c === cols - 1 ? `${40 + ((r * 13) % 50)}%` : "6rem"} h="0.8rem" /></td>
        ))}</tr>
      ))}
    </tbody>
  </table>
);

function shape(kind: SkeletonKind) {
  switch (kind) {
    case "list": return <Hits n={5} />;
    case "tiles": return (  // the Home page's six count cells (styles: .home-tiles)
      <section className="home-tiles">
        {Array.from({ length: 6 }, (_, i) => (
          <span key={i}><Bar w="4rem" h="0.7rem" /><Bar w="2.2rem" h="2rem" /><Bar w="6.5rem" h="0.7rem" /></span>
        ))}
      </section>
    );
    case "entity": return (
      <>
        <Head />
        <section className="panel"><Lines n={4} last="70%" /></section>
        <div className="two-col">
          <section className="card sk-pad"><Bar w="8rem" h="1rem" /><Bar w="100%" h="300px" /></section>
          <section className="card sk-pad"><Bar w="7rem" h="1rem" /><Lines n={5} last="40%" /></section>
        </div>
      </>
    );
    case "topic": return (
      <>
        <Head />
        <div className="row wrap">{Array.from({ length: 6 }, (_, i) => <Bar key={i} w={`${4 + (i % 3)}rem`} h="1.6rem" round />)}</div>
        <div className="sk-columns">
          {Array.from({ length: 3 }, (_, i) => (
            <section className="card sk-pad" key={i}><Bar w="6rem" h="1rem" /><Lines n={4} last="60%" /></section>
          ))}
        </div>
      </>
    );
    case "review": return (
      <div className="review-layout">
        <ol className="queue">
          {Array.from({ length: 6 }, (_, i) => (
            <li key={i} style={{ cursor: "default" }}><Bar w="3.5rem" h="0.65rem" /><Bar w={`${55 + ((i * 19) % 40)}%`} h="0.9rem" /></li>
          ))}
        </ol>
        <section className="card sk-pad">
          <div className="row"><Bar w="3.6rem" h="1.1rem" round /><Bar w="40%" h="1.3rem" /></div>
          <Lines n={5} last="45%" />
          <div className="row"><Bar w="5.5rem" h="2.1rem" round /><Bar w="5.5rem" h="2.1rem" round /><Bar w="5.5rem" h="2.1rem" round /></div>
        </section>
      </div>
    );
    case "map": return (
      <div className="map-layout">
        <section className="graph-card">
          <header><Bar w="10rem" h="0.9rem" /></header>
          <div className="graph sk-graph"><Bar w="100%" h="100%" /></div>
        </section>
        <aside className="map-side">
          <section className="card">
            <header style={{ padding: "0.55rem 0.9rem", borderBottom: "1px solid var(--line)" }}><Bar w="6rem" h="0.9rem" /></header>
            <ul className="topic-list">
              {Array.from({ length: 9 }, (_, i) => (
                <li key={i}><i style={{ background: "var(--line)" }} /><Bar w={`${40 + ((i * 29) % 45)}%`} h="0.8rem" /><Bar w="1.4rem" h="0.7rem" /></li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    );
    case "table": return <Table />;
    case "panels": return <><Panel /><Panel title="6rem" lines={3} /><Panel title="8rem" /></>;
    case "flashcard": return (
      <div className="flashcard">
        <div className="meta"><Bar w="6rem" h="0.75rem" /><Bar w="4rem" h="0.75rem" /></div>
        <Bar w="85%" h="1.6rem" /><Bar w="55%" h="1.6rem" />
        <div className="row"><Bar w="8rem" h="2.2rem" round /></div>
      </div>
    );
    case "digest": return (
      <section className="stack-sm">
        <Bar w="7rem" h="1rem" />
        {Array.from({ length: 2 }, (_, i) => (
          <div className="card pair" key={i}>
            <div className="side stack-sm"><Bar w="3.6rem" h="1.1rem" round /><Bar w="80%" h="1rem" /></div>
            <div className="link-mark"><Bar w="1.4rem" h="1.4rem" round /><Bar w="3rem" h="0.65rem" /></div>
            <div className="side stack-sm"><Bar w="3.6rem" h="1.1rem" round /><Bar w="70%" h="1rem" /></div>
          </div>
        ))}
      </section>
    );
    case "page": return (
      <>
        <div className="page-head"><div className="stack-sm"><Bar w="12rem" h="1.8rem" /><Bar w="24rem" h="0.85rem" /></div></div>
        <Hits n={4} />
      </>
    );
  }
}

/** The placeholder for `kind`; announced once to assistive technology, the boxes themselves hidden. */
export function Skeleton({ kind }: { kind: SkeletonKind }) {
  const { t } = useTranslation();
  return (
    <div className={`skeleton stack sk-${kind}`} role="status" aria-busy="true" aria-label={t("common.loading")} data-testid="skeleton">
      {shape(kind)}
    </div>
  );
}
