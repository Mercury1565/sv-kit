import { describe, expect, it } from "vitest";
import type { WaveSignalData } from "../api/types";
import { blockToElk, splitEndpoint } from "./graph";
import { buildSegments, fitLabel, formatTime, formatValue, needsFetch, niceTicks, normBits, panView, sliceBits, tToX, valueAt, xToT, zoomView } from "./waves";
import { moduleForSignal, splitPath } from "../views/waves/locate";

const view = { t0: 100, t1: 200, width: 500 };

describe("time <-> px", () => {
  it("maps linearly and round-trips", () => {
    expect(tToX(100, view)).toBe(0);
    expect(tToX(150, view)).toBe(250);
    expect(xToT(500, view)).toBe(200);
    expect(xToT(tToX(173, view), view)).toBeCloseTo(173);
  });
  it("zooms around an anchor and clamps to bounds", () => {
    const z = zoomView(view, 0.5, 150, 0, 1000);
    expect(z.t0).toBe(125);
    expect(z.t1).toBe(175);
    const out = zoomView(view, 100, 150, 0, 1000);
    expect(out.t0).toBe(0);
    expect(out.t1).toBe(1000);
    const p = panView(view, 1000, 0, 250);
    expect(p.t1).toBe(250);
    expect(p.t0).toBe(150);
  });
  it("refetches when panned out of the window or zoomed > 2x", () => {
    expect(needsFetch(view, 50, 250, 100)).toBe(false);
    expect(needsFetch({ ...view, t0: 40 }, 50, 250, 100)).toBe(true);
    expect(needsFetch({ t0: 140, t1: 160, width: 500 }, 50, 250, 100)).toBe(true);
  });
});

describe("value formatting by radix", () => {
  it("formats known values", () => {
    expect(formatValue("11111111", 8, "hex")).toBe("ff");
    expect(formatValue("11111111", 8, "dec")).toBe("255");
    expect(formatValue("11111111", 8, "signed")).toBe("-1");
    expect(formatValue("101", 3, "bin")).toBe("101");
    expect(formatValue("101", 8, "hex")).toBe("05"); // left-extended with 0
    expect(formatValue("1".repeat(64), 64, "dec")).toBe("18446744073709551615");
  });
  it("never turns unknown bits into numbers", () => {
    expect(formatValue("xxxxxxxx", 8, "hex")).toBe("xx");
    expect(formatValue("xxxx0101", 8, "hex")).toBe("x5");
    expect(formatValue("x1010101", 8, "hex")).toBe("X5");
    expect(formatValue("zzzz", 4, "dec")).toBe("z");
    expect(formatValue("1x01", 4, "dec")).toBe("X");
    expect(formatValue("x", 8, "hex")).toBe("xx"); // VCD x left-extension
  });
  it("decodes enums (decimal or bit-string keys) and falls back to hex", () => {
    expect(formatValue("010", 3, "enum", { "2": "DECODE" })).toBe("DECODE");
    expect(formatValue("010", 3, "enum", { "010": "DECODE" })).toBe("DECODE");
    expect(formatValue("111", 3, "enum", { "2": "DECODE" })).toBe("7");
  });
  it("passes reals through", () => {
    expect(formatValue("3.3000000001", 64, "hex")).toBe("3.3");
  });
  it("slices struct fields MSB-first", () => {
    expect(sliceBits("1" + "0101010" + "00000", 13, 11, 5)).toBe("0101010");
    expect(normBits("z1", 4)).toBe("zzz1");
  });
});

describe("label fitting", () => {
  it("fits, truncates, or drops", () => {
    expect(fitLabel("DECODE", 100, 7)).toBe("DECODE");
    expect(fitLabel("WRITEBACK_AND_RETIRE", 60, 7)).toBe("WRITEB…");
    expect(fitLabel("WRITEBACK", 16, 7)).toBeNull();
  });
});

describe("segments from downsampled data", () => {
  const sig: WaveSignalData = {
    width: 8,
    init: "00000000",
    changes: [
      [120, "xxxxxxxx"],
      [140, "*"],
      [160, "00000001"],
      [190, "zzzzzzzz"],
    ],
    truncated: true,
  };
  it("builds clipped segments with kinds", () => {
    const s = buildSegments(sig, view);
    expect(s.map((x) => x.kind)).toEqual(["val", "x", "dense", "val", "z"]);
    expect(s[0].x0).toBe(0);
    expect(s[1].x0).toBe(100);
    expect(s[4].x1).toBe(500);
  });
  it("collapses sub-pixel runs into dense blocks", () => {
    const busy: WaveSignalData = { width: 1, init: "0", changes: Array.from({ length: 200 }, (_, i) => [100 + i * 0.1, i % 2 ? "1" : "0"]), truncated: false };
    const s = buildSegments(busy, view);
    expect(s.length).toBeLessThan(10);
    expect(s.some((x) => x.kind === "dense")).toBe(true);
  });
  it("valueAt uses init before the first change", () => {
    expect(valueAt(sig, 110)).toBe("00000000");
    expect(valueAt(sig, 150)).toBe("*");
    expect(valueAt(sig, 160)).toBe("00000001");
  });
});

describe("time axis", () => {
  it("picks 1/2/5 steps and the right unit", () => {
    const ticks = niceTicks({ t0: 0, t1: 1000, width: 1000 }, { factor: 1, unit: "ns" });
    expect(ticks[1].t - ticks[0].t).toBe(100);
    expect(ticks[1].label).toBe("100 ns");
    const us = niceTicks({ t0: 0, t1: 100000, width: 1000 }, { factor: 1, unit: "ns" });
    expect(us[1].label).toBe("10 us");
    expect(formatTime(1500, { factor: 10, unit: "ps" })).toBe("15 ns");
  });
});

describe("design graph helpers", () => {
  it("splits node.port endpoints with dotted node ids", () => {
    const ids = new Set(["u_a", "gen[1].u_b"]);
    expect(splitEndpoint("gen[1].u_b.in", ids)).toEqual(["gen[1].u_b", "in"]);
    expect(splitEndpoint("u_a", ids)).toEqual(["u_a", null]);
    expect(splitEndpoint("clk", ids)).toEqual(["clk", null]);
  });
  it("builds an ELK graph with ports and io nodes for unknown endpoints", () => {
    const g = blockToElk({ nodes: [{ id: "u_a", label: "u_a", ports: [{ name: "y", dir: "output" }] }], edges: [{ src: "u_a.y", dst: "out_port", width: 8 }] });
    expect(g.children!.map((c) => c.id)).toEqual(["u_a", "io:out_port"]);
    expect(g.edges![0].sources).toEqual(["u_a.y"]);
  });
  it("maps wave paths to declaring modules", () => {
    expect(splitPath("tb.dut.gen[1].u_x.sig")).toEqual(["tb", "dut", "gen[1]", "u_x", "sig"]);
    const h = { top: "core", modules: { core: { ports: [], instances: [{ name: "u_exec", module: "exec" }] }, exec: { ports: [], instances: [] } } };
    expect(moduleForSignal(h, "tb.dut.u_exec.result")).toBe("exec");
    expect(moduleForSignal(h, "tb.dut.pc")).toBe("core");
  });
});
