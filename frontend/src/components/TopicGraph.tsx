// SPDX-License-Identifier: Apache-2.0
import { NodeSquareProgram } from "@sigma/node-square";
import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { useEffect, useRef, useState } from "react";
import Sigma from "sigma";
import type { Settings } from "sigma/settings";
import type { TopicMap } from "../api";
import { go } from "../router";
import { communityColor, cssVar, graphFont, ink, tint } from "./colors";

type LabelData = { x: number; y: number; size: number; label?: string | null; color?: string };

/** Modernist labels: plain ink type to the right of the square, no halo. */
function drawLabel(ctx: CanvasRenderingContext2D, data: LabelData, settings: Settings): void {
  if (!data.label) return;
  ctx.font = `${settings.labelWeight} ${settings.labelSize}px ${settings.labelFont}`;
  ctx.fillStyle = settings.labelColor.color ?? ink();
  ctx.fillText(data.label, data.x + data.size + 5, data.y + settings.labelSize / 3);
}

/** Hover: an accent square around the node and the label in a 2px ink-ruled box. */
function drawHover(ctx: CanvasRenderingContext2D, data: LabelData, settings: Settings): void {
  const size = settings.labelSize, pad = 6;
  const bg = cssVar("--bg", "#ffffff"), inkCol = cssVar("--ink", "#1c1f1a"), accent = cssVar("--accent", "#ec3013");
  ctx.lineWidth = 2;
  ctx.strokeStyle = accent;
  ctx.strokeRect(data.x - data.size - 3, data.y - data.size - 3, data.size * 2 + 6, data.size * 2 + 6);
  if (!data.label) return;
  ctx.font = `700 ${size}px ${settings.labelFont}`;
  const w = ctx.measureText(data.label).width;
  const x = data.x + data.size + 5, top = data.y - size / 2 - pad;
  ctx.fillStyle = bg;
  ctx.fillRect(x - pad, top, w + pad * 2, size + pad * 2);
  ctx.strokeStyle = inkCol;
  ctx.strokeRect(x - pad, top, w + pad * 2, size + pad * 2);
  ctx.fillStyle = inkCol;
  ctx.fillText(data.label, x, data.y + size / 3);
}

/** Topic layer only (WebGL), aggregated server-side: square nodes sized by assets per topic,
 * coloured by community; candidates are washed toward the ground. */
export default function TopicGraph({ map, onHover }: { map: TopicMap; onHover?: (id: number | null) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const g = new Graph({ type: "directed", multi: false });
    const bg = cssVar("--bg", "#ffffff");
    const edgeCol = cssVar("--line-strong");
    map.nodes.forEach((n, i) => {
      const angle = (2 * Math.PI * i) / Math.max(1, map.nodes.length);
      g.addNode(String(n.id), {
        label: n.name, x: Math.cos(angle), y: Math.sin(angle), size: 4 + Math.sqrt(n.size) * 3,
        // candidates keep their community hue but washed out, so an early (all-candidate) library is still readable
        color: n.status === "candidate" ? tint(communityColor(n.community), bg, 0.55) : communityColor(n.community),
        type: "square",
      });
    });
    map.edges.forEach((e) => {
      const s = String(e.src), d = String(e.dst);
      if (g.hasNode(s) && g.hasNode(d) && !g.hasEdge(s, d)) g.addEdge(s, d, { size: 1.2, color: edgeCol, type: "arrow" });
    });
    if (g.order > 1) forceAtlas2.assign(g, { iterations: 250, settings: { ...forceAtlas2.inferSettings(g), gravity: 1.5, scalingRatio: 6 } });
    let sigma: Sigma;
    try {
      sigma = new Sigma(g, ref.current, {
        nodeProgramClasses: { square: NodeSquareProgram }, defaultNodeType: "square",
        renderEdgeLabels: false, labelRenderedSizeThreshold: 0, labelColor: { color: ink() },
        labelFont: graphFont(), labelSize: 12, labelWeight: "600", defaultEdgeType: "arrow",
        defaultDrawNodeLabel: drawLabel, defaultDrawNodeHover: drawHover,
      });
    } catch (e) {
      setFailed(String((e as Error)?.message ?? e)); // no WebGL (old GPU driver, remote desktop)
      return;
    }
    let hovered: string | null = null;
    sigma.setSetting("nodeReducer", (node, data) => {
      if (!hovered) return data;
      if (node === hovered || g.areNeighbors(node, hovered)) return { ...data, zIndex: 1, highlighted: node === hovered };
      return { ...data, color: cssVar("--line"), label: "" };
    });
    sigma.setSetting("edgeReducer", (edge, data) => {
      if (!hovered) return data;
      return g.hasExtremity(edge, hovered) ? { ...data, color: cssVar("--ink"), size: 2 } : { ...data, hidden: true };
    });
    sigma.on("enterNode", ({ node }) => { hovered = node; onHover?.(Number(node)); sigma.refresh(); });
    sigma.on("leaveNode", () => { hovered = null; onHover?.(null); sigma.refresh(); });
    sigma.on("clickNode", ({ node }) => go(`topic/${node}`));
    return () => sigma.kill();
  }, [map, onHover]);
  if (failed) {
    return (
      <div className="graph tall stack-sm" style={{ overflow: "auto", padding: "1rem" }}>
        <p className="muted small">{failed}</p>
        <ul className="plain">{map.nodes.map((n) => <li key={n.id}><a href={`#/topic/${n.id}`}>{n.name}</a> <span className="muted small">{n.size}</span></li>)}</ul>
      </div>
    );
  }
  return <div className="graph tall" ref={ref} />;
}
