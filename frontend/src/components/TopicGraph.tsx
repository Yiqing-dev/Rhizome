// SPDX-License-Identifier: Apache-2.0
import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { useEffect, useRef } from "react";
import Sigma from "sigma";
import type { TopicMap } from "../api";
import { go } from "../router";
import { communityColor, cssVar, ink, tint } from "./colors";

/** Topic layer only (WebGL), aggregated server-side; node size = assets per topic, colour = community. */
export default function TopicGraph({ map, onHover }: { map: TopicMap; onHover?: (id: number | null) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const g = new Graph({ type: "directed", multi: false });
    const bg = cssVar("--surface", "#ffffff");
    const edgeCol = cssVar("--line-strong");
    map.nodes.forEach((n, i) => {
      const angle = (2 * Math.PI * i) / Math.max(1, map.nodes.length);
      g.addNode(String(n.id), {
        label: n.name, x: Math.cos(angle), y: Math.sin(angle), size: 5 + Math.sqrt(n.size) * 3.2,
        // candidates keep their community hue but washed out, so an early (all-candidate) library is still readable
        color: n.status === "candidate" ? tint(communityColor(n.community), bg, 0.55) : communityColor(n.community),
        type: "circle",
      });
    });
    map.edges.forEach((e) => {
      const s = String(e.src), d = String(e.dst);
      if (g.hasNode(s) && g.hasNode(d) && !g.hasEdge(s, d)) g.addEdge(s, d, { size: 1.4, color: edgeCol, type: "arrow" });
    });
    if (g.order > 1) forceAtlas2.assign(g, { iterations: 250, settings: { ...forceAtlas2.inferSettings(g), gravity: 1.5, scalingRatio: 6 } });
    const sigma = new Sigma(g, ref.current, {
      renderEdgeLabels: false, labelRenderedSizeThreshold: 0, labelColor: { color: ink() },
      labelFont: getComputedStyle(document.documentElement).fontFamily.split(",")[0].replace(/"/g, ""), labelSize: 12,
      labelWeight: "500", defaultEdgeType: "arrow",
    });
    let hovered: string | null = null;
    sigma.setSetting("nodeReducer", (node, data) => {
      if (!hovered) return data;
      if (node === hovered || g.areNeighbors(node, hovered)) return { ...data, zIndex: 1, highlighted: node === hovered };
      return { ...data, color: cssVar("--line"), label: "" };
    });
    sigma.setSetting("edgeReducer", (edge, data) => {
      if (!hovered) return data;
      return g.hasExtremity(edge, hovered) ? { ...data, color: ink(), size: 2 } : { ...data, hidden: true };
    });
    sigma.on("enterNode", ({ node }) => { hovered = node; onHover?.(Number(node)); sigma.refresh(); });
    sigma.on("leaveNode", () => { hovered = null; onHover?.(null); sigma.refresh(); });
    sigma.on("clickNode", ({ node }) => go(`topic/${node}`));
    return () => sigma.kill();
  }, [map, onHover]);
  return <div className="graph tall" ref={ref} />;
}
