import { describe, expect, it } from "vitest";
import type { CompareResult } from "../api/types";
import { compareCsv, compareRows, computeDelta } from "./compare";
import { compareImage } from "../views/Compare";

const cmp: CompareResult = {
  run_ids: ["r_0001", "r_0002"],
  labels: ["baseline, \"tt\"", "r_0002"],
  metrics: [
    computeDelta("wns", "higher", [12, -30], "ps"),
    computeDelta("area_total", "lower", [8210, 7640], "um^2"),
    computeDelta("cell_count", "lower", [900, 880], null),
    computeDelta("vt_mix.HVT", "none", [100, 120], "cells"),
    computeDelta("area_by_hier.alu", "lower", [3000, 2900], "um^2"),
  ],
};

describe("compare Basic / Advanced", () => {
  it("Basic stops at Cells; Advanced has every row, in order", () => {
    expect(compareRows(cmp, "basic").map((m) => m.metric)).toEqual(["wns", "area_total", "cell_count"]);
    expect(compareRows(cmp, "advanced").map((m) => m.metric)).toEqual(cmp.metrics.map((m) => m.metric));
  });
});

describe("compare exports", () => {
  it("CSV: one row per metric, raw values, change, % and verdict per run after the baseline", () => {
    const lines = compareCsv(cmp, compareRows(cmp, "basic")).trim().split("\r\n");
    expect(lines[0]).toBe('metric,name,unit,better,"r_0001 baseline, ""tt"" (baseline)",r_0002,r_0002 change,r_0002 change %,r_0002 verdict');
    expect(lines[1]).toBe("wns,Worst setup slack,ps,higher,12,-30,-42,-350,worse");
    expect(lines[3]).toBe("cell_count,Cells,,lower,900,880,-20,-2.2222222222222223,better");
    expect(lines).toHaveLength(4);
  });

  it("image: a header row and one row per metric shown, deltas toned by verdict", () => {
    const img = compareImage(cmp, compareRows(cmp, "advanced"), "advanced");
    expect(img.header).toHaveLength(3);
    expect(img.rows).toHaveLength(5);
    expect(img.rows[0][0][0].text).toBe("Worst setup slack (ps)");
    expect(img.rows[0][2][1]).toMatchObject({ tone: "bad" });
    expect(img.rows[1][2][1]).toMatchObject({ tone: "good" });
    expect(img.rows[0][1]).toHaveLength(1);                    // the baseline has no change line
    expect(img.subtitle).toContain("Advanced");
  });
});
