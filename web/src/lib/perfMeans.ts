// Averages of CPI / TPI over a batch's tests, to compare batches with each other.
import type { PerfBatch, PerfBatchResult } from "../api/types";
import { fmtPs, isNum } from "./format";

export type MeanKind = "arith" | "geo";
export type MeanMetric = "cpi" | "tpi";

/** Arithmetic mean of the finite numbers, null when there are none. */
export function arithMean(xs: (number | null | undefined)[]): number | null {
  const v = xs.filter(isNum);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}

/** Geometric mean of the positive numbers (log space, so long products never overflow); null when none. */
export function geoMean(xs: (number | null | undefined)[]): number | null {
  const v = xs.filter((x): x is number => isNum(x) && x > 0);
  return v.length ? Math.exp(v.reduce((a, b) => a + Math.log(b), 0) / v.length) : null;
}

export interface BatchMeans {
  batch: PerfBatch;
  /** the results averaged (all of the batch's, or only the tests every compared batch ran) */
  results: PerfBatchResult[];
  cpi: { arith: number | null; geo: number | null; n: number };
  tpi: { arith: number | null; geo: number | null; n: number };
  /** smallest and largest clock period among the averaged results (null when none is known) */
  period: [number, number] | null;
}

/** Tests every one of the batches has a CPI for, sorted. */
export function commonTests(batches: PerfBatch[]): string[] {
  if (!batches.length) return [];
  const sets = batches.map((b) => new Set(b.results.filter((r) => isNum(r.cpi)).map((r) => r.test)));
  return [...sets[0]].filter((t) => sets.every((s) => s.has(t))).sort();
}

/** True when the batches did not all run the same tests (so their own means average different programs). */
export function testSetsDiffer(batches: PerfBatch[]): boolean {
  const key = (b: PerfBatch) => b.results.filter((r) => isNum(r.cpi)).map((r) => r.test).sort().join("\u0000");
  return new Set(batches.map(key)).size > 1;
}

/** The means of one batch; `only` limits them to those tests. */
export function batchMeans(batch: PerfBatch, only?: Set<string>): BatchMeans {
  const results = only ? batch.results.filter((r) => only.has(r.test)) : batch.results;
  const cpis = results.map((r) => r.cpi).filter(isNum);
  const tpis = results.map((r) => r.tpi).filter(isNum);
  const ps = results.map((r) => r.period).filter(isNum);
  return {
    batch,
    results,
    cpi: { arith: arithMean(cpis), geo: geoMean(cpis), n: cpis.length },
    tpi: { arith: arithMean(tpis), geo: geoMean(tpis), n: tpis.length },
    period: ps.length ? [Math.min(...ps), Math.max(...ps)] : null,
  };
}

/** "4,000 ps" or "3,900–4,000 ps" (the clock periods a batch's runs finished with). */
export function periodRange(p: [number, number] | null): string {
  if (!p) return "no period";
  if (p[0] === p[1]) return fmtPs(p[0]);
  const [lo, hi] = [fmtPs(p[0]), fmtPs(p[1])];
  const unit = hi.slice(hi.lastIndexOf(" "));
  return lo.endsWith(unit) ? `${lo.slice(0, -unit.length)}–${hi}` : `${lo} – ${hi}`;
}

/** What names a batch on its bar: its name, else its id. */
export function batchName(b: PerfBatch): string {
  return b.group_name || b.group_id;
}
