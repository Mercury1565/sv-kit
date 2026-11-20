import { describe, expect, it } from "vitest";
import type { PerfBatch } from "../api/types";
import { arithMean, batchMeans, batchName, commonTests, geoMean, periodRange, testSetsDiffer } from "./perfMeans";

const batch = (id: string, rows: [string, number | null, number | null][], name?: string): PerfBatch => ({
  group_id: id, group_name: name ?? null, created_at: 1e9, status: "done",
  results: rows.map(([test, cpi, period]) => ({ test, run_id: `r_${test}`, cpi, period, tpi: cpi != null && period != null ? cpi * period : null })),
});

describe("perf means", () => {
  it("arithmetic and geometric means skip what they cannot use", () => {
    expect(arithMean([1, 2, 3, null, Number.NaN])).toBe(2);
    expect(geoMean([1, 4])).toBeCloseTo(2, 12);
    expect(geoMean([2, 8, 0, -1, null])).toBeCloseTo(4, 12);   // zero and negatives have no log
    expect(arithMean([])).toBeNull();
    expect(geoMean([0])).toBeNull();
    expect(geoMean(Array(400).fill(1e300))).toBeCloseTo(1e300, -290);   // no overflow
  });

  it("per batch: both means of CPI and TPI, test counts, the period range", () => {
    const m = batchMeans(batch("g_1", [["a", 1, 1000], ["b", 4, 2000], ["c", 2, null]]));
    expect(m.cpi).toEqual({ arith: 7 / 3, geo: 2, n: 3 });
    expect(m.tpi.n).toBe(2);
    expect(m.tpi.arith).toBe(4500);
    expect(m.tpi.geo).toBeCloseTo(Math.sqrt(1000 * 8000), 9);
    expect(m.period).toEqual([1000, 2000]);
    expect(periodRange(m.period)).toBe("1,000–2,000 ps");
    expect(periodRange([4000, 4000])).toBe("4,000 ps");
    expect(periodRange([9000, 12000])).toBe("9,000 ps – 12 ns");
    expect(periodRange(null)).toBe("no period");
  });

  it("only the tests every batch ran, when asked", () => {
    const a = batch("g_1", [["a", 1, 1000], ["b", 4, 1000]]);
    const b = batch("g_2", [["b", 2, 1000], ["c", 9, 1000]], "after");
    expect(testSetsDiffer([a, b])).toBe(true);
    expect(testSetsDiffer([a, a])).toBe(false);
    expect(commonTests([a, b])).toEqual(["b"]);
    expect(batchMeans(a, new Set(["b"])).cpi).toEqual({ arith: 4, geo: 4, n: 1 });
    expect(batchName(b)).toBe("after");
    expect(batchName(a)).toBe("g_1");
  });
});
