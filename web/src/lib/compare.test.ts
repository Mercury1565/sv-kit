import { describe, expect, it } from "vitest";
import { compareCell, computeDelta, withBaseline } from "./compare";
import { fmtDuration, fmtNum, fmtPs, fmtSigned, middleTruncate } from "./format";
import { buildHierRows } from "./hier";

describe("compare formatting", () => {
  const wns = computeDelta("wns", "higher", [12, -30, -410, null], "ps");
  const area = computeDelta("area_total", "lower", [8210, 7640, 8230, Number.NaN], "um^2");

  it("computes deltas, pct and verdicts against the first run", () => {
    expect(wns.deltas).toEqual([0, -42, -422, null]);
    expect(wns.verdict).toEqual(["na", "worse", "worse", "na"]);
    expect(area.verdict).toEqual(["na", "better", "worse", "na"]);
    expect(area.pct[1]).toBeCloseTo(-6.943, 2);
  });

  it("formats cells with sign, glyph and pct (color is never the only signal)", () => {
    const base = compareCell(wns, 0);
    expect(base.delta).toBeNull();
    expect(base.value).toBe("12");
    const c = compareCell(wns, 1);
    expect(c.value).toBe("−30");
    expect(c.delta).toBe("−42");
    expect(c.pct).toBe("−350%");
    expect(c.glyph).toBe("▼");
    const b = compareCell(area, 1);
    expect(b.glyph).toBe("▲");
    expect(b.delta).toBe("−570");
    const missing = compareCell(wns, 3);
    expect(missing.value).toBe("—");
    expect(missing.delta).toBe("—");
    expect(missing.glyph).toBe("");
  });

  it("power uses 3 decimals, counts none", () => {
    const p = computeDelta("power.total", "lower", [3.1, 2.9], "mW");
    expect(compareCell(p, 1).delta).toBe("−0.2");
    const c = computeDelta("cell_count", "lower", [1000, 1000]);
    expect(compareCell(c, 1).glyph).toBe("=");
  });

  it("withBaseline moves the chosen run first", () => {
    expect(withBaseline(["a", "b", "c"], 2)).toEqual(["c", "a", "b"]);
    expect(withBaseline(["a", "b"], 0)).toEqual(["a", "b"]);
  });
});

describe("number formatting", () => {
  it("never prints raw floats, NaN or undefined", () => {
    expect(fmtNum(0.1 + 0.2)).toBe("0.3");
    expect(fmtNum(null)).toBe("—");
    expect(fmtNum(Number.NaN)).toBe("NaN");
    expect(fmtNum(1234567.891)).toBe("1,234,568");
    expect(fmtSigned(-123456.7)).toBe("−123,457");
    expect(fmtPs(-123456.7, true)).toBe("−123.46 ns");
    expect(fmtDuration(3 * 86400 + 4000)).toBe("3d 1h");
    expect(middleTruncate("tb.dut.u_core.gen_lanes[12].u_lane.u_fwd_unit.bypass_select_ex_mem_q", 30)).toMatch(/…/);
  });
});

describe("area hierarchy tree", () => {
  it("nests '/' paths, keeps generate indices, sorts by area", () => {
    const rows = buildHierRows(
      { "": 100, u_core: 80, "u_core/u_a": 30, "u_core/u_b": 50, "u_core/gen_l[1].u_x": 5 },
      { expanded: new Set(["", "u_core"]), sort: "area", dir: 1 },
    );
    expect(rows.map((r) => r.name)).toEqual(["(top)", "u_core", "u_b", "u_a", "gen_l[1].u_x"]);
    expect(rows[1].pct).toBe(80);
    expect(rows[2].depth).toBe(2);
  });
});
