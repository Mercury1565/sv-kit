// Schematic drawing for the Design view (Blocks and Gates use the same code).
//
//   server netlist (Yosys JSON) -> Web Worker: netlistsvg + elkjs 0.3.0 + our skin -> SVG text
//   -> DOMPurify (SVG profile; no scripts, handlers, <style> or foreign tags) -> page
//   -> decorate(): critical-path highlight, tooltips, clickable instances, bus wires.
import DOMPurify from "dompurify";
import type { Schematic, SchematicCell } from "../api/types";

export interface DrawResult {
  svg: string;
  ms: number;
}

export interface DrawJob {
  promise: Promise<DrawResult>;
  cancel: () => void;
}

type Pending = { resolve: (r: DrawResult) => void; reject: (e: Error) => void };

let worker: Worker | null = null;
let seq = 0;
const pending = new Map<number, Pending>();

function getWorker(): Worker {
  if (!worker) {
    worker = new Worker(new URL("./schematic.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (e: MessageEvent<{ id: number; svg?: string; ms?: number; error?: string }>) => {
      const p = pending.get(e.data.id);
      if (!p) return;
      pending.delete(e.data.id);
      if (e.data.error != null || e.data.svg == null) p.reject(new Error(e.data.error ?? "drawing failed"));
      else p.resolve({ svg: e.data.svg, ms: e.data.ms ?? 0 });
    };
    worker.onerror = (e) => {
      e.preventDefault();
      failAll(new Error(e.message || "the drawing worker crashed"));
      worker?.terminate();
      worker = null;
    };
  }
  return worker;
}

function failAll(err: Error) {
  for (const p of pending.values()) p.reject(err);
  pending.clear();
}

/** Lay out and draw a schematic netlist off the main thread. Cancelling a running layout restarts
 * the worker: ELK runs synchronously inside it, so there is no other way to stop a big one. */
export function draw(netlist: unknown): DrawJob {
  const id = ++seq;
  let done = false;
  const promise = new Promise<DrawResult>((resolve, reject) => {
    pending.set(id, {
      resolve: (r) => ((done = true), resolve(r)),
      reject: (e) => ((done = true), reject(e)),
    });
    getWorker().postMessage({ id, netlist });
  });
  return {
    promise,
    cancel: () => {
      if (done || !pending.has(id)) return;
      pending.delete(id);
      if (pending.size === 0 && worker) {
        worker.terminate();
        worker = null;
      }
    },
  };
}

/** netlistsvg output -> safe SVG markup. Only SVG elements/attributes survive; the skin's
 * presentation comes from the page's CSS (.schematic), so <style> is dropped too. */
export function sanitize(svg: string): string {
  return DOMPurify.sanitize(svg, {
    USE_PROFILES: { svg: true },
    FORBID_TAGS: ["style", "foreignObject", "a", "image", "use"],
    FORBID_ATTR: ["style", "href", "xlink:href"],
  });
}

export const cellKey = (el: Element | null): string | null => {
  const g = el?.closest?.('g[id^="cell_"]');
  return g ? g.id.slice(5) : null;
};

function tooltip(key: string, c: SchematicCell | undefined): string {
  if (!c) return key;
  const lines = [c.name || key];
  if (c.kind === "instance" && c.module) lines.push(`module ${c.module}`);
  if (c.kind === "cell" && c.type) lines.push(`cell ${c.type}`);
  if (c.kind === "port") lines.push(c.type === "cone output" ? "cone output" : c.dir ? `${c.dir} port` : c.type ?? "port");
  if (c.kind === "logic") lines.push("assign / always blocks of this module");
  if (c.delay != null) lines.push(`cell delay ${c.delay.toFixed(1)} ps (critical path)`);
  else if (c.on_path) lines.push("on the critical path");
  return lines.join("\n");
}

/** Mark up a drawn schematic in place: size, viewBox, path highlight, tooltips, click targets. */
export function decorate(svg: SVGSVGElement, s: Pick<Schematic, "cells" | "path_nets">, opts: { clickable?: (c: SchematicCell) => boolean } = {}) {
  const w = Number(svg.getAttribute("width")) || 0;
  const h = Number(svg.getAttribute("height")) || 0;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.dataset.w = String(w);
  svg.dataset.h = String(h);
  svg.setAttribute("role", "img");
  svg.querySelectorAll<SVGGElement>('g[id^="cell_"]').forEach((g) => {
    const key = g.id.slice(5);
    const c = s.cells[key];
    g.classList.add("cell", `k-${c?.kind ?? "other"}`);
    if (c?.on_path) g.classList.add("on");
    const t = document.createElementNS("http://www.w3.org/2000/svg", "title");
    t.textContent = tooltip(key, c);
    g.prepend(t);
    if (c && opts.clickable?.(c)) {
      g.classList.add("click");
      g.setAttribute("tabindex", "0");
      g.setAttribute("role", "button");
      g.setAttribute("aria-label", `Open ${c.name} (${c.module})`);
    }
  });
  const onNets = new Set(s.path_nets);
  svg.querySelectorAll<SVGElement>("line, circle").forEach((el) => {
    const cls = Array.from(el.classList).find((x) => x.startsWith("net_"));
    if (!cls) return;
    el.classList.add("wire");
    if (cls.includes(",")) el.classList.add("bus");
    if (onNets.has(cls)) el.classList.add("on");
  });
  return { width: w, height: h };
}

export function setZoom(svg: SVGSVGElement, zoom: number) {
  const w = Number(svg.dataset.w) || 0;
  const h = Number(svg.dataset.h) || 0;
  svg.setAttribute("width", String(Math.round(w * zoom)));
  svg.setAttribute("height", String(Math.round(h * zoom)));
}
