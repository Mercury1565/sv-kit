import type { CompareResult, MetricDelta, Verdict } from "../api/types";
import { toCsv } from "./download";
import { DASH, fmtNum, fmtPct, fmtSigned, isNum, metricLabel } from "./format";

export interface CompareCell {
  value: string;
  delta: string | null; // null for the baseline column
  pct: string | null;
  verdict: Verdict;
  glyph: "▲" | "▼" | "=" | "";
  /** Accessible text, e.g. "−30 (−3.7%), worse". */
  title: string;
}

/** Digits per metric so deltas line up and never print float noise. */
export function metricDigits(m: MetricDelta): number {
  if (m.metric.startsWith("power")) return 3;
  if (m.metric === "cell_count" || m.metric.startsWith("vt_mix")) return 0;
  return 1;
}

export function compareCell(m: MetricDelta, i: number): CompareCell {
  const digits = metricDigits(m);
  const v = m.values[i];
  const verdict = (m.verdict[i] ?? "na") as Verdict;
  const value = isNum(v) ? fmtNum(v, digits) : v === null || v === undefined ? DASH : fmtNum(v, digits);
  if (i === 0) {
    return { value, delta: null, pct: null, verdict: "na", glyph: "", title: `${value} (baseline)` };
  }
  const d = m.deltas[i];
  const p = m.pct[i];
  const delta = isNum(d) ? fmtSigned(d, digits) : DASH;
  const pct = isNum(p) ? fmtPct(p, 1) : null;
  // ▲ = better, ▼ = worse: glyph carries the meaning, color only reinforces it.
  const glyph = verdict === "better" ? "▲" : verdict === "worse" ? "▼" : verdict === "same" ? "=" : "";
  const title = `${value}; change ${delta}${pct ? ` (${pct})` : ""}; ${verdict === "na" ? "not comparable" : verdict}`;
  return { value, delta, pct, verdict, glyph, title };
}

/** Move run `idx` to the front: the API treats the first id as baseline. */
export function withBaseline(ids: string[], idx: number): string[] {
  if (idx <= 0 || idx >= ids.length) return ids.slice();
  return [ids[idx], ...ids.slice(0, idx), ...ids.slice(idx + 1)];
}

/**
 * Client-side delta computation (used by the mock layer, mirrors the backend rule:
 * delta = v - v0, pct = delta / |v0| * 100, verdict by `better`).
 */
export function computeDelta(metric: string, better: MetricDelta["better"], values: (number | null)[], unit?: string | null): MetricDelta {
  const v0 = values[0];
  const deltas: (number | null)[] = [];
  const pct: (number | null)[] = [];
  const verdict: Verdict[] = [];
  values.forEach((v, i) => {
    if (i === 0) {
      deltas.push(isNum(v) ? 0 : null);
      pct.push(isNum(v) ? 0 : null);
      verdict.push("na");
      return;
    }
    if (!isNum(v) || !isNum(v0)) {
      deltas.push(null);
      pct.push(null);
      verdict.push("na");
      return;
    }
    const d = v - v0;
    deltas.push(d);
    pct.push(v0 !== 0 ? (d / Math.abs(v0)) * 100 : null);
    if (better === "none") verdict.push("na");
    else if (Math.abs(d) < 1e-9) verdict.push("same");
    else verdict.push((better === "higher" ? d > 0 : d < 0) ? "better" : "worse");
  });
  return { metric, unit: unit ?? null, better, values, deltas, pct, verdict };
}

// ------------------------------------------------------------------ Basic / Advanced, exports
export type CompareLevel = "basic" | "advanced";

/**
 * Basic = the headline rows, timing through cell count ("Cells"); Advanced adds the rest
 * (cells per transistor speed class, area per top-level block, and anything a backend adds later).
 */
export const BASIC_METRICS: ReadonlySet<string> = new Set([
  "wns", "tns", "hold_wns", "hold_tns", "period", "area_total", "power.total", "power.dynamic", "power.leakage", "cell_count",
]);

export function compareRows(cmp: CompareResult, level: CompareLevel): MetricDelta[] {
  return level === "advanced" ? cmp.metrics : cmp.metrics.filter((m) => BASIC_METRICS.has(m.metric));
}

/**
 * The comparison as CSV: one row per metric, raw numbers. Per run: its value; for every run after
 * the baseline also the change, the change in percent and the verdict (better / worse / same / na).
 */
export function compareCsv(cmp: CompareResult, metrics: MetricDelta[]): string {
  const header = ["metric", "name", "unit", "better"];
  cmp.run_ids.forEach((id, i) => {
    const tag = `${id}${cmp.labels[i] && cmp.labels[i] !== id ? ` ${cmp.labels[i]}` : ""}`;
    header.push(i === 0 ? `${tag} (baseline)` : tag);
    if (i > 0) header.push(`${id} change`, `${id} change %`, `${id} verdict`);
  });
  const rows = metrics.map((m) => {
    const r: unknown[] = [m.metric, metricLabel(m.metric), m.unit ?? "", m.better];
    m.values.forEach((v, i) => {
      r.push(v);
      if (i > 0) r.push(m.deltas[i], m.pct[i], m.verdict[i] ?? "na");
    });
    return r;
  });
  return toCsv(header, rows);
}

// ------------------------------------------------------------------ reordering columns
/** Move the item at `from` to position `to` (the others shift to make room). */
export function moveItem<T>(xs: readonly T[], from: number, to: number): T[] {
  const out = xs.slice();
  if (from < 0 || from >= xs.length || to < 0 || to >= xs.length || from === to) return out;
  const [x] = out.splice(from, 1);
  out.splice(to, 0, x);
  return out;
}

/**
 * The same comparison with its columns in a new order, without asking the server. Valid only while the
 * baseline (column 0) stays first: every change is measured against it, so the cells just move along.
 */
export function permuteCompare(cmp: CompareResult, from: number, to: number): CompareResult {
  const order = moveItem(cmp.run_ids.map((_, i) => i), from, to);
  const pick = <T,>(xs: T[]) => order.map((i) => xs[i]);
  return {
    run_ids: pick(cmp.run_ids),
    labels: pick(cmp.labels),
    metrics: cmp.metrics.map((m) => ({ ...m, values: pick(m.values), deltas: pick(m.deltas), pct: pick(m.pct), verdict: pick(m.verdict) })),
  };
}
