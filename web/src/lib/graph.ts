// Block / cone graphs → ELK input graphs, and ELK output → drawable shapes.
import type { ElkExtendedEdge, ElkNode, ElkPort } from "elkjs/lib/elk-api";
import type { BlockGraph, ConeGraph, DesignPort } from "../api/types";

export const CHAR_W = 7.2; // 12px monospace
const PORT_H = 14;

export interface DrawNode {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  sub?: string;
  module?: string | null;
  onPath?: boolean;
  delay?: number | null;
  io?: boolean;
  ports: { id: string; name: string; x: number; y: number; side: "W" | "E" }[];
}

export interface DrawEdge {
  id: string;
  points: { x: number; y: number }[];
  label?: string;
  width?: number | null;
  onPath?: boolean;
}

export interface Drawing {
  width: number;
  height: number;
  nodes: DrawNode[];
  edges: DrawEdge[];
}

const textW = (s: string) => Math.ceil(s.length * CHAR_W);

function portOf(p: DesignPort | string): DesignPort {
  return typeof p === "string" ? { name: p } : p;
}

const isIn = (dir?: string | null) => !dir || /^in/i.test(dir);

/** Split an edge endpoint "node.port" into [node, port] using the known node ids (ids may contain dots). */
export function splitEndpoint(ep: string, nodeIds: Set<string>): [string, string | null] {
  if (nodeIds.has(ep)) return [ep, null];
  for (let i = ep.lastIndexOf("."); i > 0; i = ep.lastIndexOf(".", i - 1)) {
    const n = ep.slice(0, i);
    if (nodeIds.has(n)) return [n, ep.slice(i + 1)];
  }
  return [ep, null];
}

const LAYOUT = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.spacing.nodeNodeBetweenLayers": "60",
  "elk.spacing.nodeNode": "28",
  "elk.spacing.edgeEdge": "8",
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.portConstraints": "FIXED_SIDE",
};

export function blockToElk(g: BlockGraph): ElkNode {
  const ids = new Set(g.nodes.map((n) => n.id));
  const extra = new Map<string, ElkNode>(); // boundary/unknown endpoints become small io nodes
  const portIds = new Set<string>();
  const children: ElkNode[] = g.nodes.map((n) => {
    const ports = (n.ports ?? []).map(portOf);
    const ins = ports.filter((p) => isIn(p.dir));
    const outs = ports.filter((p) => !isIn(p.dir));
    const longestIn = Math.max(0, ...ins.map((p) => textW(p.name)));
    const longestOut = Math.max(0, ...outs.map((p) => textW(p.name)));
    const w = Math.max(longestIn + longestOut + 40, textW(n.label), textW(n.module ?? ""), 100);
    const h = Math.max(28, 10 + Math.max(ins.length, outs.length) * PORT_H);
    const elkPorts: ElkPort[] = ports.map((p) => {
      const id = `${n.id}.${p.name}`;
      portIds.add(id);
      return { id, width: 6, height: 6, layoutOptions: { "elk.port.side": isIn(p.dir) ? "WEST" : "EAST" } } as ElkPort;
    });
    // keep ports below the two header lines (instance + module name)
    return {
      id: n.id,
      width: w,
      height: h,
      ports: elkPorts,
      layoutOptions: { "elk.portConstraints": "FIXED_SIDE", "elk.nodeLabels.placement": "OUTSIDE" },
    } as ElkNode;
  });
  const ref = (ep: string, asSource: boolean): string => {
    const [node, port] = splitEndpoint(ep, ids);
    if (ids.has(node)) {
      if (port) {
        const pid = `${node}.${port}`;
        if (!portIds.has(pid)) {
          portIds.add(pid);
          children.find((c) => c.id === node)!.ports!.push({ id: pid, width: 6, height: 6, layoutOptions: { "elk.port.side": asSource ? "EAST" : "WEST" } } as ElkPort);
        }
        return pid;
      }
      return node;
    }
    if (!extra.has(ep)) extra.set(ep, { id: `io:${ep}`, width: textW(ep) + 16, height: 22 });
    return `io:${ep}`;
  };
  const edges: ElkExtendedEdge[] = g.edges.map((e, i) => ({
    id: `e${i}`,
    sources: [ref(e.src, true)],
    targets: [ref(e.dst, false)],
    labels: e.width && e.width > 1 ? [{ text: String(e.width), width: textW(String(e.width)), height: 12 }] : [],
  }));
  return { id: "root", layoutOptions: { ...LAYOUT, "elk.spacing.nodeNode": "44", "elk.padding": "[top=24,left=12,bottom=24,right=12]" }, children: [...children, ...extra.values()], edges };
}

export function coneToElk(g: ConeGraph): ElkNode {
  const ids = new Set(g.nodes.map((n) => n.id));
  const extra = new Map<string, ElkNode>();
  const children: ElkNode[] = g.nodes.map((n) => ({
    id: n.id,
    width: Math.max(textW(n.id), textW(n.cell ?? ""), textW(n.delay != null ? `${n.delay.toFixed(1)} ps` : "")) + 20,
    height: n.delay != null ? 50 : 38,
  }));
  const ref = (ep: string) => {
    const [node] = splitEndpoint(ep, ids);
    if (ids.has(node)) return node;
    if (!extra.has(ep)) extra.set(ep, { id: `io:${ep}`, width: textW(ep) + 16, height: 22 });
    return `io:${ep}`;
  };
  const edges: ElkExtendedEdge[] = g.edges.map((e, i) => ({ id: `e${i}`, sources: [ref(e.src)], targets: [ref(e.dst)] }));
  return { id: "root", layoutOptions: { ...LAYOUT, "elk.portConstraints": "FREE" }, children: [...children, ...extra.values()], edges };
}

/** ELK result → drawing, enriched with labels/highlights from the source graph. */
export function toDrawing(
  laid: ElkNode,
  info: (id: string) => Partial<Pick<DrawNode, "label" | "sub" | "module" | "onPath" | "delay">>,
  edgeInfo: (i: number) => Partial<Pick<DrawEdge, "label" | "width" | "onPath">>,
): Drawing {
  const nodes: DrawNode[] = (laid.children ?? []).map((c) => {
    const io = c.id.startsWith("io:");
    const base = io ? { label: c.id.slice(3) } : info(c.id);
    return {
      id: c.id,
      x: c.x ?? 0,
      y: c.y ?? 0,
      w: c.width ?? 0,
      h: c.height ?? 0,
      label: base.label ?? c.id,
      sub: base.sub,
      module: base.module,
      onPath: base.onPath,
      delay: base.delay,
      io,
      ports: (c.ports ?? []).map((p) => ({
        id: p.id,
        name: p.id.slice(c.id.length + 1),
        x: (c.x ?? 0) + (p.x ?? 0),
        y: (c.y ?? 0) + (p.y ?? 0),
        side: (p.x ?? 0) < (c.width ?? 0) / 2 ? "W" : "E",
      })),
    };
  });
  const edges: DrawEdge[] = (laid.edges ?? []).map((e, i) => {
    const pts: { x: number; y: number }[] = [];
    for (const s of e.sections ?? []) {
      pts.push(s.startPoint, ...(s.bendPoints ?? []), s.endPoint);
    }
    return { id: e.id, points: pts, ...edgeInfo(i) };
  });
  return { width: (laid.width ?? 0) + 20, height: (laid.height ?? 0) + 20, nodes, edges };
}

let elkPromise: Promise<{ layout: (g: ElkNode) => Promise<ElkNode> }> | null = null;

export function getElk() {
  if (!elkPromise) {
    elkPromise = import("elkjs/lib/elk.bundled.js").then((m) => {
      const ELK = (m as unknown as { default: new () => { layout: (g: ElkNode) => Promise<ElkNode> } }).default;
      return new ELK();
    });
  }
  return elkPromise;
}
