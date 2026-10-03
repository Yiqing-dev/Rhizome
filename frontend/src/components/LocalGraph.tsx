// SPDX-License-Identifier: Apache-2.0
import cytoscape from "cytoscape";
import { useEffect, useRef } from "react";
import type { GraphView } from "../api";
import { go } from "../router";

const TYPE_COLORS: Record<string, string> = {
  work: "#4f6bed", dataset: "#1b9e77", method: "#d95f02", idea: "#7570b3", claim: "#e7298a",
  topic: "#66a61e", organism: "#a6761d", modality: "#666666",
};
const EDGE_COLORS: Record<string, string> = {
  about: "#66a61e", applicable_to: "#a6d854", proposes: "#7570b3", uses: "#1b9e77", produces: "#0f7a5a",
  evaluates: "#d95f02", supports: "#4daf4a", contradicts: "#e41a1c", extends: "#ff7f00", cites: "#999999",
  is_a: "#377eb8", of_organism: "#a6761d", of_modality: "#888888",
};

/** 1-2 hop neighbourhood, edges coloured by type. The server caps the element count. */
export default function LocalGraph({ graph, center }: { graph: GraphView; center: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const cy = cytoscape({
      container: ref.current,
      elements: [
        ...graph.nodes.map((n) => ({ data: { id: String(n.id), label: n.name.slice(0, 40), type: n.type } })),
        ...graph.edges.map((e) => ({ data: { id: `e${e.id}`, source: String(e.src), target: String(e.dst), type: e.type } })),
      ],
      style: [
        { selector: "node", style: { label: "data(label)", "font-size": 9, color: "#555", width: 14, height: 14,
          "background-color": (n: cytoscape.NodeSingular) => TYPE_COLORS[n.data("type")] ?? "#999" } },
        { selector: `node[id = "${center}"]`, style: { width: 24, height: 24, "border-width": 2, "border-color": "#222" } },
        { selector: "edge", style: { width: 1.5, "curve-style": "bezier", "target-arrow-shape": "triangle",
          "arrow-scale": 0.7, "line-color": (e: cytoscape.EdgeSingular) => EDGE_COLORS[e.data("type")] ?? "#bbb",
          "target-arrow-color": (e: cytoscape.EdgeSingular) => EDGE_COLORS[e.data("type")] ?? "#bbb" } },
      ],
      layout: { name: "cose", animate: false, nodeDimensionsIncludeLabels: true },
    });
    cy.on("tap", "node", (evt) => {
      const n = graph.nodes.find((x) => String(x.id) === evt.target.id());
      if (n) go(n.type === "topic" ? `topic/${n.id}` : `entity/${n.id}`);
    });
    return () => cy.destroy();
  }, [graph, center]);
  return <div className="graph" ref={ref} />;
}

export { EDGE_COLORS, TYPE_COLORS };
