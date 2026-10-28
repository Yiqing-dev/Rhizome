// SPDX-License-Identifier: Apache-2.0
import { NodeSquareProgram } from "@sigma/node-square";
import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import noverlap from "graphology-layout-noverlap";
import { useEffect, useRef, useState } from "react";
import Sigma from "sigma";
import type { Settings } from "sigma/settings";
import { useTranslation } from "react-i18next";
import type { TopicMap } from "../api";
import { go } from "../router";
import { communityColor, cssVar, graphFont, tint } from "./colors";

type LabelData = { x: number; y: number; size: number; label?: string | null; color?: string };

/** Modernist labels: plain ink type to the right of the square, no halo. */
function drawLabel(ctx: CanvasRenderingContext2D, data: LabelData, settings: Settings): void {
  if (!data.label) return;
  ctx.font = `${settings.labelWeight} ${settings.labelSize}px ${settings.labelFont}`;
  ctx.fillStyle = settings.labelColor.color ?? "#1c1f1a";
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

/** Positions: a phyllotaxis spiral by size (the biggest topics in the middle) as the start, the
 * force layout on top of it when there are is_a edges to pull families together, then a
 * no-overlap pass so squares never sit on each other. */
function layout(g: Graph): void {
  const ids = g.nodes().sort((a, b) => g.getNodeAttribute(b, "size") - g.getNodeAttribute(a, "size"));
  const golden = Math.PI * (3 - Math.sqrt(5));
  ids.forEach((id, i) => {
    const r = 16 * Math.sqrt(i + 1), t = i * golden;
    g.setNodeAttribute(id, "x", r * Math.cos(t));
    g.setNodeAttribute(id, "y", r * Math.sin(t));
  });
  const n = g.order;
  if (g.size > 0 && n > 1) {
    forceAtlas2.assign(g, {
      iterations: Math.min(700, 150 + n * 2),
      settings: { ...forceAtlas2.inferSettings(g), gravity: 0.12, scalingRatio: 60, adjustSizes: true, barnesHutOptimize: n > 150, slowDown: 4, outboundAttractionDistribution: true },
    });
  }
  noverlap.assign(g, { maxIterations: 200, settings: { margin: 12, ratio: 1.8, expansion: 1.4 } });
}

/** Topic layer only (WebGL), aggregated server-side: square nodes sized by assets per topic,
 * coloured by community (a community of one stays neutral); candidates are washed toward the
 * ground. `focus` centres and highlights a node (the list beside the map drives it). */
export default function TopicGraph({ map, onHover, focus }: { map: TopicMap; onHover?: (id: number | null) => void; focus?: number | null }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const hoverRef = useRef<{ set: (id: string | null) => void } | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const g = new Graph({ type: "directed", multi: false });
    const bg = cssVar("--bg", "#ffffff"), inkCol = cssVar("--ink", "#1c1f1a");
    const neutral = tint(inkCol, bg, 0.6), edgeCol = tint(inkCol, bg, 0.72), dim = tint(inkCol, bg, 0.88);
    const members = new Map<number, number>();
    map.nodes.forEach((n) => { if (n.community !== null) members.set(n.community, (members.get(n.community) ?? 0) + 1); });
    const colourOf = (n: TopicMap["nodes"][number]) =>
      n.community !== null && (members.get(n.community) ?? 0) >= 2 ? communityColor(n.community) : neutral;
    map.nodes.forEach((n) => {
      g.addNode(String(n.id), {
        label: n.name, x: 0, y: 0, size: 4 + Math.sqrt(n.size) * 3,
        color: n.status === "candidate" ? tint(colourOf(n), bg, 0.55) : colourOf(n), type: "square",
      });
    });
    map.edges.forEach((e) => {
      const s = String(e.src), d = String(e.dst);
      if (g.hasNode(s) && g.hasNode(d) && !g.hasEdge(s, d)) g.addEdge(s, d, { size: 1.2, color: edgeCol, type: "arrow" });
    });
    layout(g);
    let sigma: Sigma;
    try {
      sigma = new Sigma(g, ref.current, {
        nodeProgramClasses: { square: NodeSquareProgram }, defaultNodeType: "square",
        renderEdgeLabels: false, labelColor: { color: inkCol },
        labelFont: graphFont(), labelSize: 12, labelWeight: "600", defaultEdgeType: "arrow",
        // the biggest topic in each cell of a coarse grid gets a label; zooming in shows more
        labelRenderedSizeThreshold: 5, labelDensity: 0.16, labelGridCellSize: 140,
        defaultDrawNodeLabel: drawLabel, defaultDrawNodeHover: drawHover,
      });
    } catch (e) {
      setFailed(String((e as Error)?.message ?? e)); // no WebGL (old GPU driver, remote desktop)
      return;
    }
    sigmaRef.current = sigma;
    let hovered: string | null = null;
    sigma.setSetting("nodeReducer", (node, data) => {
      if (!hovered) return data;
      if (node === hovered || g.areNeighbors(node, hovered)) return { ...data, zIndex: 1, highlighted: node === hovered, forceLabel: true };
      return { ...data, color: dim, label: "" };
    });
    sigma.setSetting("edgeReducer", (edge, data) => {
      if (!hovered) return data;
      return g.hasExtremity(edge, hovered) ? { ...data, color: inkCol, size: 2 } : { ...data, hidden: true };
    });
    const setHover = (id: string | null) => { hovered = id; sigma.refresh(); };
    hoverRef.current = { set: setHover };
    sigma.on("enterNode", ({ node }) => { setHover(node); onHover?.(Number(node)); });
    sigma.on("leaveNode", () => { setHover(null); onHover?.(null); });
    sigma.on("clickNode", ({ node }) => go(`topic/${node}`));
    return () => { sigmaRef.current = null; hoverRef.current = null; sigma.kill(); };
  }, [map, onHover]);

  useEffect(() => {  // the list beside the map: hover a row to find its square
    const sigma = sigmaRef.current;
    if (!sigma) return;
    if (focus === null || focus === undefined || !sigma.getGraph().hasNode(String(focus))) { hoverRef.current?.set(null); return; }
    hoverRef.current?.set(String(focus));
    const d = sigma.getNodeDisplayData(String(focus));
    if (d) sigma.getCamera().animate({ x: d.x, y: d.y, ratio: Math.min(sigma.getCamera().ratio, 0.5) }, { duration: 300 });
  }, [focus]);

  const cam = (fn: (c: ReturnType<Sigma["getCamera"]>) => void) => { const s = sigmaRef.current; if (s) fn(s.getCamera()); };
  if (failed) {
    return (
      <div className="graph tall stack-sm" style={{ overflow: "auto", padding: "1rem" }}>
        <p className="muted small">{failed}</p>
        <ul className="plain">{map.nodes.map((n) => <li key={n.id}><a href={`#/topic/${n.id}`}>{n.name}</a> <span className="muted small">{n.size}</span></li>)}</ul>
      </div>
    );
  }
  return (
    <div className="graph-wrap">
      <div className="graph tall" ref={ref} />
      <div className="graph-controls" role="group" aria-label={t("map.zoom")}>
        <button type="button" onClick={() => cam((c) => c.animatedZoom({ duration: 200 }))} title={t("map.zoom_in")} aria-label={t("map.zoom_in")}>+</button>
        <button type="button" onClick={() => cam((c) => c.animatedUnzoom({ duration: 200 }))} title={t("map.zoom_out")} aria-label={t("map.zoom_out")}>−</button>
        <button type="button" onClick={() => cam((c) => c.animatedReset({ duration: 250 }))} title={t("map.fit")} aria-label={t("map.fit")}>⤢</button>
      </div>
    </div>
  );
}
