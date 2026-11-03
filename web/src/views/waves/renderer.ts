// Imperative canvas renderer for waveforms. React hands it rows / cursor / scroll; pan and
// zoom mutate the view here and redraw on requestAnimationFrame, so continuous interaction
// never re-renders React (animation-performance: no per-frame state updates).
import type { WaveData, WaveSignalData } from "../../api/types";
import {
  buildSegments,
  fitLabel,
  formatValue,
  needsFetch,
  niceTicks,
  panView,
  sliceBits,
  tToX,
  xToT,
  zoomView,
  type EnumMap,
  type Radix,
  type Timescale,
  type View,
} from "../../lib/waves";

export const ROW_H = 22;

export interface RenderRow {
  key: string;
  path: string; // signal path whose data to use
  width: number; // bit width of this row (field width for fields)
  radix: Radix;
  enumMap?: EnumMap | null;
  field?: { msb: number; lsb: number; parentWidth: number };
  real?: boolean;
}

export type Fetcher = (signals: string[], t0: number, t1: number, maxChanges: number) => Promise<WaveData>;

interface Cache {
  f0: number;
  f1: number;
  span: number;
  data: WaveSignalData;
}

interface Colors {
  bg: string;
  grid: string;
  line: string;
  busFill: string;
  text: string;
  x: string;
  z: string;
  dense: string;
  cursor: string;
  marker: string;
  sel: string;
  muted: string;
  font: string;
}

function readColors(): Colors {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string, d: string) => cs.getPropertyValue(n).trim() || d;
  return {
    bg: v("--w-bg", "#fff"),
    grid: v("--w-grid", "#eee"),
    line: v("--w-line", "#15803d"),
    busFill: v("--w-bus-fill", "rgba(21,128,61,.06)"),
    text: v("--w-text", "#111"),
    x: v("--w-x", "#d32f2f"),
    z: v("--w-z", "#b7791f"),
    dense: v("--w-dense", "#7c8a9a"),
    cursor: v("--w-cursor", "#2f6fde"),
    marker: v("--w-marker", "#c026d3"),
    sel: v("--w-sel", "rgba(47,111,222,.08)"),
    muted: v("--fg-muted", "#5b6573"),
    font: v("--mono", "monospace"),
  };
}

export interface RendererCallbacks {
  onViewChange?: (v: View) => void; // throttled by the caller
  onData?: () => void;
  onWidth?: (path: string, width: number) => void;
  onError?: (e: unknown) => void;
}

export class WaveRenderer {
  private ctx: CanvasRenderingContext2D | null;
  private actx: CanvasRenderingContext2D | null;
  private dpr = 1;
  private w = 0;
  private h = 0;
  private colors: Colors;
  private hatch: CanvasPattern | null = null;
  private raf = 0;
  private fetchTimer: ReturnType<typeof setTimeout> | null = null;
  private fetchSeq = 0;
  private charW = 7;
  private cache = new Map<string, Cache>();
  private fieldCache = new WeakMap<WaveSignalData, Map<string, WaveSignalData>>();

  rows: RenderRow[] = [];
  scrollTop = 0;
  view: View = { t0: 0, t1: 1, width: 1 };
  bounds = { lo: 0, hi: 1 };
  ts: Timescale = { factor: 1, unit: "ns" };
  cursor: number | null = null;
  marker: number | null = null;
  selected: string | null = null;

  constructor(
    private canvas: HTMLCanvasElement,
    private axis: HTMLCanvasElement,
    private fetcher: Fetcher,
    private cb: RendererCallbacks = {},
  ) {
    this.ctx = canvas.getContext("2d");
    this.actx = axis.getContext("2d");
    this.colors = readColors();
  }

  refreshTheme(): void {
    this.colors = readColors();
    this.hatch = null;
    this.schedule();
  }

  destroy(): void {
    cancelAnimationFrame(this.raf);
    if (this.fetchTimer) clearTimeout(this.fetchTimer);
    this.fetchSeq++;
  }

  // ---------------------------------------------------------------- inputs
  resize(width: number, height: number): void {
    this.dpr = window.devicePixelRatio || 1;
    this.w = Math.max(1, Math.floor(width));
    this.h = Math.max(1, Math.floor(height));
    for (const [c, hh] of [
      [this.canvas, this.h],
      [this.axis, 24],
    ] as const) {
      c.width = Math.round(this.w * this.dpr);
      c.height = Math.round(hh * this.dpr);
      c.style.width = `${this.w}px`;
      c.style.height = `${hh}px`;
    }
    this.view = { ...this.view, width: this.w };
    this.scheduleFetch();
    this.schedule();
  }

  setBounds(lo: number, hi: number, ts: Timescale): void {
    this.bounds = { lo, hi: Math.max(hi, lo + 1) };
    this.ts = ts;
  }

  setView(t0: number, t1: number, notify = true): void {
    const span = Math.max(1, t1 - t0);
    this.view = panView({ t0, t1: t0 + span, width: this.w || 1 }, 0, this.bounds.lo, this.bounds.hi);
    if (span > this.bounds.hi - this.bounds.lo) this.view = { t0: this.bounds.lo, t1: this.bounds.hi, width: this.w || 1 };
    this.changed(notify);
  }

  fit(): void {
    this.setView(this.bounds.lo, this.bounds.hi);
  }

  zoom(factor: number, atX?: number): void {
    const at = atX === undefined ? (this.cursor !== null && this.cursor >= this.view.t0 && this.cursor <= this.view.t1 ? this.cursor : (this.view.t0 + this.view.t1) / 2) : xToT(atX, this.view);
    this.view = zoomView(this.view, factor, at, this.bounds.lo, this.bounds.hi, Math.max(1e-6, 4 / Math.max(1, this.w)));
    this.changed(true);
  }

  pan(dxPx: number): void {
    this.view = panView(this.view, dxPx, this.bounds.lo, this.bounds.hi);
    this.changed(true);
  }

  /** Bring t into view without changing zoom (no animation: keyboard navigation). */
  reveal(t: number): void {
    const span = this.view.t1 - this.view.t0;
    if (t < this.view.t0 || t > this.view.t1) this.setView(t - span / 2, t + span / 2);
  }

  setRows(rows: RenderRow[]): void {
    this.rows = rows;
    this.scheduleFetch();
    this.schedule();
  }

  setScroll(top: number): void {
    this.scrollTop = top;
    this.scheduleFetch();
    this.schedule();
  }

  setCursor(t: number | null, marker: number | null, selected: string | null): void {
    this.cursor = t;
    this.marker = marker;
    this.selected = selected;
    this.schedule();
  }

  private changed(notify: boolean) {
    this.scheduleFetch();
    this.schedule();
    if (notify) this.cb.onViewChange?.(this.view);
  }

  // ---------------------------------------------------------------- data
  visibleRange(): [number, number] {
    const first = Math.max(0, Math.floor(this.scrollTop / ROW_H));
    const last = Math.min(this.rows.length, Math.ceil((this.scrollTop + this.h) / ROW_H) + 1);
    return [first, last];
  }

  /** Signal data for a row (fields are sliced from the parent's data, memoized). */
  rowData(r: RenderRow): WaveSignalData | undefined {
    const c = this.cache.get(r.path);
    if (!c) return undefined;
    if (!r.field) return c.data;
    let m = this.fieldCache.get(c.data);
    if (!m) {
      m = new Map();
      this.fieldCache.set(c.data, m);
    }
    const k = `${r.field.msb}:${r.field.lsb}`;
    let d = m.get(k);
    if (!d) {
      const pw = r.field.parentWidth;
      const sl = (v: string) => (v === "*" ? "*" : sliceBits(v, pw, r.field!.msb, r.field!.lsb));
      const changes: [number, string][] = [];
      let prev = sl(c.data.init);
      for (const [t, v] of c.data.changes) {
        const s = sl(v);
        if (s !== prev || s === "*") changes.push([t, s]);
        prev = s;
      }
      d = { width: r.width, init: sl(c.data.init), changes, truncated: c.data.truncated };
      m.set(k, d);
    }
    return d;
  }

  hasData(path: string): boolean {
    return this.cache.has(path);
  }

  invalidate(): void {
    this.cache.clear();
    this.scheduleFetch();
  }

  private scheduleFetch() {
    if (this.fetchTimer) clearTimeout(this.fetchTimer);
    this.fetchTimer = setTimeout(() => this.fetch(), 40);
  }

  private async fetch() {
    if (!(this.w > 1)) return;
    const [a, b] = this.visibleRange();
    const span = this.view.t1 - this.view.t0;
    const want = new Set<string>();
    for (let i = a; i < b; i++) {
      const p = this.rows[i]?.path;
      if (!p) continue;
      const c = this.cache.get(p);
      if (!c || needsFetch(this.view, c.f0, c.f1, c.span)) want.add(p);
    }
    if (!want.size) return;
    const f0 = Math.max(this.bounds.lo, this.view.t0 - span / 2);
    const f1 = Math.min(this.bounds.hi, this.view.t1 + span / 2);
    const maxChanges = Math.max(64, Math.round((this.w * (f1 - f0)) / span));
    const seq = ++this.fetchSeq;
    try {
      const res = await this.fetcher(Array.from(want), f0, f1, maxChanges);
      for (const [p, d] of Object.entries(res.signals ?? {})) {
        // a newer request superseding this one may already have filled it; keep the finer one
        const cur = this.cache.get(p);
        if (cur && seq < this.fetchSeq && !needsFetch(this.view, cur.f0, cur.f1, cur.span)) continue;
        this.cache.set(p, { f0, f1, span, data: d });
        if (d.width) this.cb.onWidth?.(p, d.width);
      }
      this.cb.onData?.();
      this.schedule();
    } catch (e) {
      this.cb.onError?.(e);
    }
  }

  // ---------------------------------------------------------------- draw
  schedule(): void {
    if (this.raf) return;
    this.raf = requestAnimationFrame(() => {
      this.raf = 0;
      this.draw();
    });
  }

  private hatchPattern(ctx: CanvasRenderingContext2D): CanvasPattern | null {
    if (this.hatch) return this.hatch;
    const s = Math.round(6 * this.dpr);
    const c = document.createElement("canvas");
    c.width = c.height = s;
    const g = c.getContext("2d");
    if (!g) return null;
    g.strokeStyle = this.colors.x;
    g.globalAlpha = 0.55;
    g.lineWidth = Math.max(1, this.dpr);
    g.beginPath();
    g.moveTo(0, s);
    g.lineTo(s, 0);
    g.moveTo(-s / 2, s / 2);
    g.lineTo(s / 2, -s / 2);
    g.moveTo(s / 2, s * 1.5);
    g.lineTo(s * 1.5, s / 2);
    g.stroke();
    this.hatch = ctx.createPattern(c, "repeat");
    this.hatch?.setTransform?.(new DOMMatrix().scale(1 / this.dpr));
    return this.hatch;
  }

  draw(): void {
    const ctx = this.ctx;
    if (!ctx) return;
    const C = this.colors;
    const { w, h } = this;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = C.bg;
    ctx.fillRect(0, 0, w, h);
    ctx.font = `12px ${C.font}`;
    this.charW = ctx.measureText("0").width || 7;
    ctx.textBaseline = "middle";

    const ticks = niceTicks(this.view, this.ts);
    // grid
    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (const t of ticks) {
      const x = Math.round(t.x) + 0.5;
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
    }
    ctx.stroke();

    const [a, b] = this.visibleRange();
    for (let i = a; i < b; i++) {
      const r = this.rows[i];
      const y = i * ROW_H - this.scrollTop;
      if (r.key === this.selected) {
        ctx.fillStyle = C.sel;
        ctx.fillRect(0, y, w, ROW_H);
      }
      ctx.strokeStyle = C.grid;
      ctx.beginPath();
      ctx.moveTo(0, y + ROW_H - 0.5);
      ctx.lineTo(w, y + ROW_H - 0.5);
      ctx.stroke();
      const d = this.rowData(r);
      if (!d) {
        ctx.fillStyle = C.muted;
        ctx.fillText("…", 6, y + ROW_H / 2);
        continue;
      }
      if (r.width === 1 && !r.real) this.drawBit(ctx, d, y);
      else this.drawBus(ctx, r, d, y);
    }

    // cursor + marker
    const vline = (t: number | null, color: string, dash: number[]) => {
      if (t === null || t < this.view.t0 || t > this.view.t1) return;
      const x = Math.round(tToX(t, this.view)) + 0.5;
      ctx.strokeStyle = color;
      ctx.setLineDash(dash);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
      ctx.setLineDash([]);
    };
    vline(this.marker, C.marker, [4, 3]);
    vline(this.cursor, C.cursor, []);
    this.drawAxis(ticks);
  }

  private drawBit(ctx: CanvasRenderingContext2D, d: WaveSignalData, y: number) {
    const C = this.colors;
    const top = y + 4;
    const bot = y + ROW_H - 5;
    const mid = (top + bot) / 2;
    const segs = buildSegments(d, this.view);
    ctx.lineWidth = 1.25;
    let prevY: number | null = null;
    for (const s of segs) {
      const x0 = s.x0;
      const x1 = s.x1;
      if (s.kind === "dense") {
        ctx.fillStyle = C.dense;
        ctx.globalAlpha = 0.45;
        ctx.fillRect(x0, top, Math.max(1, x1 - x0), bot - top);
        ctx.globalAlpha = 1;
        prevY = null;
        continue;
      }
      if (s.kind === "x") {
        const p = this.hatchPattern(ctx);
        ctx.fillStyle = p ?? C.x;
        ctx.fillRect(x0, top, x1 - x0, bot - top);
        ctx.strokeStyle = C.x;
        ctx.strokeRect(x0 + 0.5, top + 0.5, Math.max(0, x1 - x0 - 1), bot - top - 1);
        prevY = null;
        continue;
      }
      if (s.kind === "z") {
        ctx.strokeStyle = C.z;
        ctx.beginPath();
        ctx.moveTo(x0, mid);
        ctx.lineTo(x1, mid);
        ctx.stroke();
        prevY = mid;
        continue;
      }
      const ly = s.value === "1" ? top : bot;
      ctx.strokeStyle = C.line;
      ctx.beginPath();
      if (prevY !== null && prevY !== ly) {
        ctx.moveTo(x0, prevY);
        ctx.lineTo(x0, ly);
      } else ctx.moveTo(x0, ly);
      ctx.lineTo(x1, ly);
      ctx.stroke();
      if (s.value === "1") {
        ctx.fillStyle = C.busFill;
        ctx.fillRect(x0, top, x1 - x0, bot - top);
      }
      prevY = ly;
    }
  }

  private drawBus(ctx: CanvasRenderingContext2D, r: RenderRow, d: WaveSignalData, y: number) {
    const C = this.colors;
    const top = y + 4;
    const bot = y + ROW_H - 5;
    const mid = (top + bot) / 2;
    const segs = buildSegments(d, this.view);
    ctx.lineWidth = 1.25;
    for (const s of segs) {
      const x0 = s.x0;
      const x1 = s.x1;
      const wpx = x1 - x0;
      if (s.kind === "dense") {
        ctx.fillStyle = C.dense;
        ctx.globalAlpha = 0.45;
        ctx.fillRect(x0, top, Math.max(1, wpx), bot - top);
        ctx.globalAlpha = 1;
        continue;
      }
      if (s.kind === "z") {
        ctx.strokeStyle = C.z;
        ctx.beginPath();
        ctx.moveTo(x0, mid);
        ctx.lineTo(x1, mid);
        ctx.stroke();
        const lbl = fitLabel("z", wpx, this.charW);
        if (lbl) {
          ctx.fillStyle = C.z;
          ctx.fillText(lbl, x0 + 4, mid - 5);
        }
        continue;
      }
      const slant = Math.min(3, wpx / 2);
      ctx.beginPath();
      ctx.moveTo(x0, mid);
      ctx.lineTo(x0 + slant, top);
      ctx.lineTo(x1 - slant, top);
      ctx.lineTo(x1, mid);
      ctx.lineTo(x1 - slant, bot);
      ctx.lineTo(x0 + slant, bot);
      ctx.closePath();
      if (s.kind === "x") {
        ctx.fillStyle = this.hatchPattern(ctx) ?? C.x;
        ctx.fill();
        ctx.strokeStyle = C.x;
      } else {
        ctx.fillStyle = C.busFill;
        ctx.fill();
        ctx.strokeStyle = C.line;
      }
      ctx.stroke();
      if (wpx > this.charW * 2 + 8) {
        const text = formatValue(s.value, r.width, r.radix, r.enumMap);
        const lbl = fitLabel(text, wpx - slant * 2, this.charW);
        if (lbl) {
          ctx.fillStyle = s.kind === "x" ? C.x : C.text;
          ctx.fillText(lbl, Math.max(0, x0) + slant + 4, mid + 0.5);
        }
      }
    }
  }

  private drawAxis(ticks: ReturnType<typeof niceTicks>) {
    const ctx = this.actx;
    if (!ctx) return;
    const C = this.colors;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = C.bg;
    ctx.fillRect(0, 0, this.w, 24);
    ctx.font = `11px ${C.font}`;
    ctx.textBaseline = "middle";
    ctx.strokeStyle = C.muted;
    ctx.fillStyle = C.muted;
    ctx.beginPath();
    for (const t of ticks) {
      const x = Math.round(t.x) + 0.5;
      ctx.moveTo(x, 18);
      ctx.lineTo(x, 24);
      ctx.fillText(t.label, x + 3, 10);
    }
    ctx.stroke();
    const tri = (t: number | null, color: string) => {
      if (t === null || t < this.view.t0 || t > this.view.t1) return;
      const x = tToX(t, this.view);
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(x - 5, 16);
      ctx.lineTo(x + 5, 16);
      ctx.lineTo(x, 24);
      ctx.fill();
    };
    tri(this.marker, C.marker);
    tri(this.cursor, C.cursor);
  }
}
