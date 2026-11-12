import { describe, expect, it } from "vitest";
import { periodNote, periodShort, periodSourceText, periodsDiffer } from "./perfPeriod";
import { historyOption } from "../views/PerfHistory";
import { perfChartOption } from "../views/Perf";
import type { ChartTheme } from "../components/EChart";

const T = { fg: "#000", muted: "#555", faint: "#999", border: "#ddd", bg: "#fff", accent: "#00f", good: "#0a0", bad: "#a00", warn: "#aa0", mono: "monospace", series: Array(8).fill("#123456") } as ChartTheme;

describe("clock period on perf charts", () => {
  it("names where the period came from", () => {
    expect(periodSourceText("svkit.toml")).toBe("from svkit.toml");
    expect(periodSourceText("sweep:g_0002")).toBe("from clock sweep g_0002");
    expect(periodSourceText("synth:r_0001")).toBe("from synthesis r_0001");
    expect(periodSourceText("manual")).toBe("entered by hand");
  });

  it("one shared period: stated once with its source", () => {
    const xs = [{ period: 4000, period_source: "svkit.toml" }, { period: 4000, period_source: "svkit.toml" }];
    expect(periodsDiffer(xs)).toBe(false);
    expect(periodNote(xs)).toBe("Clock period 4,000 ps (from svkit.toml)");
  });

  it("different periods: the range, and each bar carries its own", () => {
    const xs = [{ period: 4000, period_source: "svkit.toml" }, { period: 3500, period_source: "sweep:g_0002" }, { period: null }];
    expect(periodsDiffer(xs)).toBe(true);
    expect(periodNote(xs)).toBe("Clock period 3,500 ps – 4,000 ps: each run's own, under its label; 1 without one");
    expect(periodShort(xs[2])).toBe("no period");
    expect(periodNote([{ period: null }])).toMatch(/not known/);
  });

  it("is in the chart options that saved images are drawn from", () => {
    const pts = [
      { run_id: "r_0001", created_at: 1, cpi: 1.2, tpi: 4800, period: 4000, period_source: "svkit.toml" },
      { run_id: "r_0002", created_at: 2, cpi: 1.1, tpi: 3850, period: 3500, period_source: "sweep:g_0002" },
    ] as any;
    const h = historyOption("tpi", pts, "fib")(T) as any;
    expect(h.title.subtext).toContain("3,500 ps – 4,000 ps");
    expect(h.xAxis.axisLabel.formatter("r_0002", 1)).toContain("3,500 ps");
    const rows = [
      { test: "fib", cycles: 10, instrs: 8, cpi: 1.25, tpi: 5000, period: 4000, period_source: "svkit.toml" },
      { test: "gcd", cycles: 10, instrs: 9, cpi: 1.11, tpi: 4444, period: 4000, period_source: "svkit.toml" },
    ] as any;
    const m = perfChartOption("cpi", rows, "latest result per test")(T) as any;
    expect(m.title.subtext).toBe("Clock period 4,000 ps (from svkit.toml)");
    expect(m.xAxis.axisLabel.formatter("fib", 0)).toBe("fib");
  });
});
