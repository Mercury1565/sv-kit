import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, setTransport } from "../api/client";
import { MockBackend } from "../api/mock";
import type { Project, RunSummary, Suite } from "../api/types";
import { SuiteTabs, TestRunButton, latestPerTest, projectSuites, suiteOf } from "./Tests";

const SUITES: Suite[] = [
  { id: "system", label: "System tests", flow: "vcs", flows: ["vcs", "verilator"], tests: ["ex001", "queue"], unit: false, golden: true, default: true },
  { id: "unit", label: "Unit tests", flow: "unit", flows: ["unit"], tests: ["queue"], unit: true, golden: false, default: false },
];

const run = (id: string, test: string, flow: string | null, status = "passed", t = 1): RunSummary =>
  ({ id, kind: "sim", adapter: "verilator", status, created_at: t, test, flow, knobs: {}, artifacts: {}, tags: [] }) as RunSummary;

describe("test suites", () => {
  it("assigns runs to suites by flow; old runs without a flow go to the default suite", () => {
    expect(suiteOf(run("r1", "ex001", "vcs"), SUITES)).toBe("system");
    expect(suiteOf(run("r2", "ex001", "verilator"), SUITES)).toBe("system");
    expect(suiteOf(run("r3", "queue", "unit"), SUITES)).toBe("unit");
    expect(suiteOf(run("r4", "ex001", null), SUITES)).toBe("system");
  });

  it("keeps a unit test and a system test of the same name apart", () => {
    const runs = [run("r1", "queue", "unit", "failed", 2), run("r2", "queue", "vcs", "passed", 1)];
    const unit = latestPerTest(runs.filter((r) => suiteOf(r, SUITES) === "unit"), ["queue"]);
    const sys = latestPerTest(runs.filter((r) => suiteOf(r, SUITES) === "system"), ["ex001", "queue"]);
    expect(unit.map((r) => [r.test, r.run?.status])).toEqual([["queue", "failed"]]);
    expect(sys.find((r) => r.test === "queue")?.run?.status).toBe("passed");
  });

  it("falls back to one implicit suite for an older backend", () => {
    const p = { flows: { sim: ["vcs"], synth: [], sta: [] }, tests: ["a"] } as unknown as Project;
    const s = projectSuites(p);
    expect(s).toHaveLength(1);
    expect(s[0]).toMatchObject({ id: "system", flow: "vcs", tests: ["a"], default: true });
  });

  it("renders suite tabs with counts and switches by route", () => {
    render(<SuiteTabs suites={SUITES} current="unit" />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["System tests 2", "Unit tests 1"]);
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    fireEvent.click(tabs[0]);
    expect(location.hash).toBe("#/tests/system");
    expect(screen.getByRole("button", { name: "What does this mean?" })).toBeTruthy();
  });

  it("runs or reruns one test on its own, in its suite's flow", async () => {
    setTransport(new MockBackend("demo"), true);
    const submit = vi.spyOn(api, "submitJob");
    const { rerender } = render(<TestRunButton test="queue" flow="unit" ran={false} />);
    expect(screen.getByRole("button", { name: "Run queue" }).textContent).toBe("Run");
    rerender(<TestRunButton test="queue" flow="unit" ran />);
    fireEvent.click(screen.getByRole("button", { name: "Rerun queue" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith("sim", { test: "queue", flow: "unit" }));
    submit.mockRestore();
  });
});
