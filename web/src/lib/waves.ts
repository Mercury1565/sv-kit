// Pure waveform logic: time <-> px mapping, value formatting by radix, label fitting,
// and turning downsampled change lists into draw segments. No DOM here (unit tested).
import type { WaveChange, WaveSignalData } from "../api/types";

export interface View {
  t0: number; // ticks at x = 0
  t1: number; // ticks at x = width
  width: number; // CSS px
}

export type Radix = "hex" | "bin" | "dec" | "signed" | "enum";

export const tToX = (t: number, v: View): number => ((t - v.t0) / (v.t1 - v.t0 || 1)) * v.width;
export const xToT = (x: number, v: View): number => v.t0 + (x / (v.width || 1)) * (v.t1 - v.t0);

/** Zoom around anchor tick `at` by `factor` (<1 = zoom in), clamped to [lo, hi] and a minimum span. */
export function zoomView(v: View, factor: number, at: number, lo: number, hi: number, minSpan = 1): View {
  const span = Math.max(minSpan, Math.min((hi - lo) * 1.0 || minSpan, (v.t1 - v.t0) * factor));
  const frac = (at - v.t0) / (v.t1 - v.t0 || 1);
  let t0 = at - frac * span;
  let t1 = t0 + span;
  if (t0 < lo) {
    t0 = lo;
    t1 = lo + span;
  }
  if (t1 > hi) {
    t1 = hi;
    t0 = Math.max(lo, hi - span);
  }
  return { ...v, t0, t1 };
}

/** Pan by dx CSS px (positive = later times), clamped to [lo, hi]. */
export function panView(v: View, dxPx: number, lo: number, hi: number): View {
  const span = v.t1 - v.t0;
  let t0 = v.t0 + (dxPx / (v.width || 1)) * span;
  t0 = Math.max(lo, Math.min(hi - span, t0));
  if (span >= hi - lo) t0 = lo;
  return { ...v, t0, t1: t0 + span };
}

// ------------------------------------------------------------------ values
const BITS_RE = /^[01xzXZhlHLuUwW-]*$/;

export function isBits(v: string): boolean {
  return v.length > 0 && BITS_RE.test(v);
}

/** Normalize a binary string to `width` chars (VCD left-extension rules), lowercase x/z. */
export function normBits(v: string, width: number): string {
  if (!isBits(v) || width <= 0) return v;
  let s = v.toLowerCase().replace(/[uw-]/g, "x").replace(/h/g, "1").replace(/l/g, "0");
  if (s.length < width) {
    const c = s[0] === "x" || s[0] === "z" ? s[0] : "0";
    s = c.repeat(width - s.length) + s;
  } else if (s.length > width) {
    s = s.slice(s.length - width);
  }
  return s;
}

export const hasX = (v: string): boolean => /[xX]/.test(v);
export const hasZ = (v: string): boolean => /[zZ]/.test(v);
export const allZ = (v: string): boolean => /^[zZ]+$/.test(v);

/** Extract bits [msb:lsb] (MSB-first string of `width`). */
export function sliceBits(bits: string, width: number, msb: number, lsb: number): string {
  if (!isBits(bits)) return bits;
  const b = normBits(bits, width);
  const hiIdx = width - 1 - msb;
  const loIdx = width - 1 - lsb;
  if (hiIdx < 0 || loIdx >= width || hiIdx > loIdx) return "x";
  return b.slice(hiIdx, loIdx + 1);
}

function bitsToBig(b: string): bigint {
  return b.length ? BigInt("0b" + b) : 0n;
}

/** Parse an enum map key: decimal "5", "0x1f", "'b101", or a raw binary string of width bits. */
export function enumKeyToBig(k: string, width: number): bigint | null {
  const s = k.trim();
  try {
    if (/^0x[0-9a-f]+$/i.test(s)) return BigInt(s);
    if (/^(\d+)?'[sS]?[bB][01_]+$/.test(s)) return bitsToBig(s.split(/[bB]/)[1].replace(/_/g, ""));
    if (/^(\d+)?'[sS]?[hH][0-9a-fA-F_]+$/.test(s)) return BigInt("0x" + s.split(/[hH]/)[1].replace(/_/g, ""));
    if (/^(\d+)?'[sS]?[dD]\d+$/.test(s)) return BigInt(s.split(/[dD]/)[1]);
    if (/^[01]+$/.test(s) && s.length === width && width > 1) return bitsToBig(s);
    if (/^\d+$/.test(s)) return BigInt(s);
  } catch {
    return null;
  }
  return null;
}

export type EnumMap = Record<string, string>;
const enumCache = new WeakMap<EnumMap, Map<string, string>>();

function enumLookup(map: EnumMap, bits: string, width: number): string | undefined {
  let m = enumCache.get(map);
  if (!m) {
    m = new Map();
    for (const [k, name] of Object.entries(map)) {
      const n = enumKeyToBig(k, width);
      if (n !== null) m.set(n.toString(), name);
    }
    enumCache.set(map, m);
  }
  return m.get(bitsToBig(bits).toString());
}

function hexOf(b: string): string {
  let out = "";
  const pad = (4 - (b.length % 4)) % 4;
  const s = "0".repeat(pad) + b;
  for (let i = 0; i < s.length; i += 4) {
    const nib = s.slice(i, i + 4);
    if (/^[01]{4}$/.test(nib)) out += parseInt(nib, 2).toString(16);
    else if (/^x+$/.test(nib.slice(i === 0 ? pad : 0))) out += "x";
    else if (/^z+$/.test(nib.slice(i === 0 ? pad : 0))) out += "z";
    else out += hasX(nib) ? "X" : "Z";
  }
  return out;
}

/**
 * Format a wave value for display. Unknown bits never produce a number:
 * all-x → "x", partial x → "X" in the affected digits (hex) or whole value (dec).
 */
export function formatValue(raw: string, width: number, radix: Radix, enumMap?: EnumMap | null): string {
  if (raw === "*") return "";
  if (!isBits(raw)) {
    // real values / strings arrive as text
    const n = Number(raw);
    return Number.isFinite(n) ? String(Number(n.toPrecision(10))) : raw;
  }
  const b = normBits(raw, Math.max(1, width));
  if (width <= 1 && radix !== "enum") return b;
  if (hasX(b) || hasZ(b)) {
    if (radix === "bin") return b;
    if (radix === "hex") return hexOf(b);
    if (/^x+$/.test(b)) return "x";
    if (/^z+$/.test(b)) return "z";
    return hasX(b) ? "X" : "Z";
  }
  switch (radix) {
    case "bin":
      return b;
    case "dec":
      return bitsToBig(b).toString();
    case "signed": {
      const n = bitsToBig(b);
      return b[0] === "1" ? (n - (1n << BigInt(b.length))).toString() : n.toString();
    }
    case "enum": {
      const name = enumMap ? enumLookup(enumMap, b, width) : undefined;
      return name ?? hexOf(b);
    }
    default:
      return hexOf(b);
  }
}

export function defaultRadix(width: number, isEnum: boolean): Radix {
  if (isEnum) return "enum";
  return width <= 1 ? "bin" : "hex";
}

/** Value at time t: last change at or before t, else `init`. */
export function valueAt(sig: WaveSignalData, t: number): string {
  const c = sig.changes;
  let lo = 0;
  let hi = c.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (c[mid][0] <= t) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found >= 0 ? c[found][1] : sig.init;
}

// ------------------------------------------------------------------ labels
/**
 * Fit a label into `px` CSS px with monospace char width `charW`.
 * Returns the text, a truncated text ending in "…", or null if nothing useful fits.
 */
export function fitLabel(text: string, px: number, charW: number, pad = 4): string | null {
  if (!text) return null;
  const avail = px - 2 * pad;
  const maxChars = Math.floor(avail / charW);
  if (maxChars >= text.length) return text;
  if (maxChars < 2) return null;
  return text.slice(0, maxChars - 1) + "…";
}

// ------------------------------------------------------------------ segments
export type SegKind = "val" | "x" | "z" | "dense";

export interface Segment {
  x0: number;
  x1: number;
  kind: SegKind;
  value: string; // raw value ("*" for dense)
}

function kindOf(v: string): SegKind {
  if (v === "*") return "dense";
  if (!isBits(v)) return "val";
  if (allZ(v)) return "z";
  if (hasX(v) || hasZ(v)) return "x";
  return "val";
}

/**
 * Turn a signal's (possibly downsampled) change list into segments clipped to [0, view.width].
 * Runs of segments narrower than `minPx` collapse into one "dense" segment so the canvas
 * never draws sub-pixel shapes.
 */
export function buildSegments(sig: WaveSignalData, view: View, minPx = 1.5): Segment[] {
  const out: Segment[] = [];
  const pts: WaveChange[] = [];
  const startVal = valueAt(sig, view.t0);
  pts.push([view.t0, startVal]);
  // first change strictly after t0
  let lo = 0;
  let hi = sig.changes.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sig.changes[mid][0] <= view.t0) lo = mid + 1;
    else hi = mid;
  }
  for (let i = lo; i < sig.changes.length && sig.changes[i][0] < view.t1; i++) pts.push(sig.changes[i]);

  for (let i = 0; i < pts.length; i++) {
    const x0 = Math.max(0, tToX(pts[i][0], view));
    const x1 = i + 1 < pts.length ? tToX(pts[i + 1][0], view) : view.width;
    if (x1 <= x0) {
      // zero-width change: mark the previous segment dense-at-end
      const prev = out[out.length - 1];
      if (prev && prev.kind !== "dense" && prev.x1 - prev.x0 < minPx) prev.kind = "dense";
      continue;
    }
    const v = pts[i][1];
    const kind = kindOf(v);
    const prev = out[out.length - 1];
    if (prev && prev.kind === kind && prev.value === v) {
      prev.x1 = x1;
      continue;
    }
    if (x1 - x0 < minPx) {
      if (prev && (prev.kind === "dense" || prev.x1 - prev.x0 < minPx)) {
        prev.kind = "dense";
        prev.value = "*";
        prev.x1 = Math.min(view.width, x1);
        continue;
      }
    }
    out.push({ x0, x1: Math.min(view.width, x1), kind, value: v });
  }
  return out;
}

// ------------------------------------------------------------------ time axis
const UNITS: [string, number][] = [
  ["fs", 1e-15],
  ["ps", 1e-12],
  ["ns", 1e-9],
  ["us", 1e-6],
  ["ms", 1e-3],
  ["s", 1],
];

export interface Timescale {
  factor: number;
  unit: string;
}

function unitScale(u: string): number {
  const f = UNITS.find(([n]) => n === u.toLowerCase().replace("µ", "u"));
  return f ? f[1] : 1e-12;
}

/** Seconds per tick. */
export function tickSeconds(ts: Timescale): number {
  return (ts.factor || 1) * unitScale(ts.unit || "ps");
}

/** Pick the display unit so that `spanTicks` reads as a number >= 1. */
export function pickUnit(spanTicks: number, ts: Timescale): [string, number] {
  const sec = Math.abs(spanTicks) * tickSeconds(ts);
  let best = UNITS[0];
  for (const u of UNITS) if (sec >= u[1]) best = u;
  return best;
}

export function formatTime(t: number, ts: Timescale, unit?: [string, number], maxDecimals = 3): string {
  const [name, scale] = unit ?? pickUnit(Math.abs(t) || 1, ts);
  const v = (t * tickSeconds(ts)) / scale;
  const s = Number(v.toFixed(maxDecimals)).toLocaleString("en-US", { maximumFractionDigits: maxDecimals });
  return `${s} ${name}`;
}

export interface Tick {
  t: number;
  x: number;
  label: string;
}

/** Nice 1/2/5 × 10^k tick spacing with at least `minPx` between labels. */
export function niceTicks(view: View, ts: Timescale, minPx = 90): Tick[] {
  const span = view.t1 - view.t0;
  if (!(span > 0) || !(view.width > 0)) return [];
  const raw = (span * minPx) / view.width;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  let step = pow;
  for (const m of [1, 2, 5, 10]) {
    if (m * pow >= raw) {
      step = m * pow;
      break;
    }
  }
  step = Math.max(step, 1); // ticks are integers
  const unit = pickUnit(step, ts);
  const decimals = Math.max(0, Math.min(6, -Math.floor(Math.log10((step * tickSeconds(ts)) / unit[1]) + 1e-9)));
  const out: Tick[] = [];
  const first = Math.ceil(view.t0 / step) * step;
  for (let t = first; t <= view.t1 && out.length < 1000; t += step) {
    out.push({ t, x: tToX(t, view), label: formatTime(t, ts, unit, decimals) });
  }
  return out;
}

/** How far the fetched window [f0, f1] at resolution `fetchedSpan` still serves `view`. */
export function needsFetch(view: View, f0: number, f1: number, fetchedSpan: number): boolean {
  const span = view.t1 - view.t0;
  if (view.t0 < f0 || view.t1 > f1) return true;
  // zoomed in more than 2x since the fetch: re-request for finer detail
  return fetchedSpan / span > 2 || span / fetchedSpan > 2;
}
