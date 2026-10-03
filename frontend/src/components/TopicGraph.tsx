// SPDX-License-Identifier: Apache-2.0
import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { useEffect, useRef } from "react";
import Sigma from "sigma";
import type { TopicMap } from "../api";
import { go } from "../router";

const PALETTE = ["#4f6bed", "#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#1f78b4"];

/** Topic layer only (WebGL), aggregated server-side; node size = assets per topic, colour = community. */
export default function TopicGraph({ map }: { map: TopicMap }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const g = new Graph({ type: "directed", multi: false });
    map.nodes.forEach((n, i) => {
      const angle = (2 * Math.PI * i) / Math.max(1, map.nodes.length);
      g.addNode(String(n.id), {
        label: n.name, x: Math.cos(angle), y: Math.sin(angle), size: 4 + Math.sqrt(n.size) * 3,
        color: n.status === "candidate" ? "#bbbbbb" : PALETTE[(n.community ?? 0) % PALETTE.length],
      });
    });
    map.edges.forEach((e) => {
      const s = String(e.src), d = String(e.dst);
      if (g.hasNode(s) && g.hasNode(d) && !g.hasEdge(s, d)) g.addEdge(s, d, { size: 1, color: "#cccccc" });
    });
    if (g.order > 1) forceAtlas2.assign(g, { iterations: 200, settings: forceAtlas2.inferSettings(g) });
    const sigma = new Sigma(g, ref.current, { renderEdgeLabels: false, labelRenderedSizeThreshold: 0 });
    sigma.on("clickNode", ({ node }) => go(`topic/${node}`));
    return () => sigma.kill();
  }, [map]);
  return <div className="graph tall" ref={ref} />;
}
