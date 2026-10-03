// SPDX-License-Identifier: Apache-2.0
import cytoscape from "cytoscape";
import { useEffect, useRef } from "react";
import type { GraphView } from "../api";
import { go } from "../router";
import { edgeColor, edgeDashed, ink, surface, typeColor } from "./colors";

/** 1–2 hop neighbourhood, edges coloured by type. The server caps the element count. */
export default function LocalGraph({ graph, center }: { graph: GraphView; center: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const label = ink();
    const bg = surface();
    const cy = cytoscape({
      container: ref.current,
      elements: [
        ...graph.nodes.map((n) => ({
          data: { id: String(n.id), label: n.name.length > 42 ? n.name.slice(0, 40) + "…" : n.name, type: n.type,
            color: typeColor(n.type), center: n.id === center ? 1 : 0, candidate: n.status === "candidate" ? 1 : 0 },
        })),
        ...graph.edges.map((e) => ({
          data: { id: `e${e.id}`, source: String(e.src), target: String(e.dst), type: e.type, color: edgeColor(e.type),
            dash: edgeDashed(e.type) ? "dashed" : "solid", rejected: e.status === "rejected" ? 1 : 0 },
        })),
      ],
      style: [
        { selector: "node", style: {
          label: "data(label)", "font-size": 10, "font-family": getComputedStyle(document.documentElement).fontFamily,
          color: label, "text-wrap": "wrap", "text-max-width": "140", "text-margin-y": 4, "text-valign": "bottom",
          "text-background-color": bg, "text-background-opacity": 0.85, "text-background-padding": "2",
          width: 16, height: 16, "background-color": "data(color)", "border-width": 2, "border-color": bg,
        } },
        { selector: "node[candidate = 1]", style: { "background-opacity": 0.55 } },
        { selector: "node[center = 1]", style: { width: 30, height: 30, "font-size": 12, "font-weight": 600, "border-width": 3, "border-color": label } },
        { selector: "edge", style: {
          width: 1.6, "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.7,
          "line-color": "data(color)", "target-arrow-color": "data(color)", opacity: 0.85,
        } },
        { selector: "edge[dash = 'dashed']", style: { "line-style": "dashed", "line-dash-pattern": [6, 4] } },
        { selector: "edge[rejected = 1]", style: { opacity: 0.25 } },
        { selector: "node:active, node:selected", style: { "overlay-opacity": 0.08 } },
      ],
      layout: { name: "cose", animate: false, nodeDimensionsIncludeLabels: true, padding: 24, idealEdgeLength: () => 90 } as cytoscape.LayoutOptions,
      wheelSensitivity: 0.3,
    });
    cy.on("tap", "node", (evt) => {
      const n = graph.nodes.find((x) => String(x.id) === evt.target.id());
      if (n) go(n.type === "topic" ? `topic/${n.id}` : `entity/${n.id}`);
    });
    cy.on("mouseover", "node", (evt) => {
      const n = evt.target;
      cy.elements().not(n.closedNeighborhood()).style("opacity", 0.25);
    });
    cy.on("mouseout", "node", () => cy.elements().removeStyle("opacity"));
    return () => cy.destroy();
  }, [graph, center]);
  return <div className="graph" ref={ref} />;
}
