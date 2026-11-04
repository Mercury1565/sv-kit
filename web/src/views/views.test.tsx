import { describe, expect, it, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { httpTransport, setTransport } from "../api/client";
import { MockBackend } from "../api/mock";
import type { RunSummary, XTraceNode } from "../api/types";
import { VirtualTable, visibleRange } from "../components/VirtualTable";
import { CompareTable } from "./Compare";
import { RunsTable, filterRuns } from "./Runs";
import { latestPerTest } from "./Tests";
import { XTraceTree, collectRootCauses } from "./XTrace";

afterEach(() => setTransport(httpTransport));

describe("virtualized tables", () => {
  it("visibleRange covers the viewport plus overscan", () => {
    expect(visibleRange(0, 260, 26, 10000, 5)).toEqual([0, 15]);
    expect(visibleRange(26 * 5000, 260, 26, 10000, 5)).toEqual([4995, 5015]);
    expect(visibleRange(26 * 9999, 260, 26, 10000, 5)[1]).toBe(10000);
  });

  it("renders only a window of 10k rows", () => {
    const rows = Array.from({ length: 10000 }, (_, i) => ({ id: `r_${i}`, n: i }));
    const { container } = render(<VirtualTable rows={rows} columns={[{ key: "n", header: "N", width: "1fr", render: (r) => r.n }]} rowKey={(r) => r.id} />);
    const domRows = container.querySelectorAll(".vt-row");
    expect(domRows.length).toBeGreaterThan(10);
    expect(domRows.length).toBeLessThan(100);
    expect(container.querySelector("[aria-rowcount]")?.getAttribute("aria-rowcount")).toBe("10000");
  });

  it("runs table survives the worst-case dataset (10k runs, long names, NaN, nulls)", async () => {
    const be = new MockBackend("worst");
    const runs = (await be.request("GET", "/api/runs", { limit: 5000 })) as RunSummary[];
    const { container } = render(<RunsTable runs={runs} selected={new Set()} onToggle={() => {}} />);
    expect(container.querySelectorAll(".vt-row").length).toBeLessThan(100);
    expect(container.textContent).not.toMatch(/undefined|NaN/);
    // escaping: the script-tag test name renders as text
    expect(container.querySelector("script")).toBeNull();
    expect(filterRuns(runs, { kind: "synth", status: "", tag: "", text: "" }).every((r) => r.kind === "synth")).toBe(true);
  });

  it("tests view groups latest run per test, failures first", async () => {
    const be = new MockBackend("demo");
    const runs = (await be.request("GET", "/api/runs", { kind: "sim", limit: 5000 })) as RunSummary[];
    const rows = latestPerTest(runs, ["never_ran"]);
    expect(rows[0].run?.status).toBe("failed");
    expect(rows.find((r) => r.test === "never_ran")?.run).toBeUndefined();
    expect(rows.find((r) => r.test === "alu_basic")?.history.length).toBe(4);
  });
});

describe("compare table", () => {
  it("shows baseline, deltas and glyphs", async () => {
    const be = new MockBackend("demo");
    const synth = ((await be.request("GET", "/api/runs", { kind: "synth" })) as RunSummary[]).map((r) => r.id);
    const cmp = (await be.request("GET", "/api/compare", { ids: synth.slice(0, 3).join(",") })) as any;
    const { container } = render(<CompareTable cmp={cmp} />);
    expect(Array.from(container.querySelectorAll(".chip")).filter((c) => c.textContent === "baseline").length).toBe(1);
    expect(screen.getByText("WNS")).toBeTruthy();
    expect(document.body.textContent).toMatch(/[▲▼]/);
  });
});

describe("X-trace tree", () => {
  const tree: XTraceNode = {
    signal: "tb.dut.wb_data",
    time: 4175,
    value: "32'hxxxx001d",
    driver: { kind: "assign", file: "rtl/core.sv", line: 88, text: "assign wb_data = a ? b : c;" },
    children: [
      { signal: "tb.dut.acc_q", time: 4165, value: "32'hxxxxxxxx", driver: { kind: "always_ff", file: "rtl/alu.sv", line: 40 }, cause: "no_reset", note: "never reset", children: [] },
      { signal: "tb.in", time: 4175, value: "z", cause: "external_input", children: [] },
      { signal: "tb.deep", time: 1, value: "x", cause: "unknown", note: "depth bound", children: [] },
    ],
  };
  it("renders nodes with drivers, cause badges and waves links", () => {
    const { container } = render(<XTraceTree node={tree} run="r_0007" />);
    expect(screen.getAllByTestId("xt-node").length).toBe(4);
    expect(container.querySelectorAll(".root-cause").length).toBe(2); // unknown is not a root cause
    expect(screen.getByText("rtl/alu.sv:40")).toBeTruthy();
    expect(screen.getByText(/register never reset/)).toBeTruthy();
    const links = Array.from(container.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(links).toContain("#/waves/r_0007?t=4165&signals=tb.dut.acc_q");
    expect(collectRootCauses(tree).length).toBe(3);
  });
});

describe("app shell", () => {
  it("renders the empty project with CLI hints", async () => {
    setTransport(new MockBackend("empty"), true);
    const { App } = await import("../App");
    render(<App />);
    await waitFor(() => expect(screen.getByText("No runs yet")).toBeTruthy());
    expect(screen.getByText("new-project")).toBeTruthy();
    expect(document.body.textContent).toContain("svkit sim <test>");
  });
});

import { noDivergenceReason } from "./Tests";
describe("noDivergenceReason", () => {
  it("explains a failure that produced no output", () => {
    const m = noDivergenceReason({ message: "exit code 2", artifacts: { "sim.log": "sim.log" }, cached_from: null });
    expect(m).toContain("failed before producing output");
    expect(m).toContain("exit code 2");
  });
  it("explains a failure with waves but no output mismatch", () => {
    expect(noDivergenceReason({ message: "$fatal at tb.sv:10", artifacts: { "waves.vcd": "waves.vcd" } })).toContain("came from elsewhere");
  });
});
