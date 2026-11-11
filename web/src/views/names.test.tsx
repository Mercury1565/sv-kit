import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, httpTransport, setTransport } from "../api/client";
import { MockBackend } from "../api/mock";
import type { Group, RunSummary, Suite } from "../api/types";
import { NameEditor } from "../components/NameEditor";
import { batchLabel, TestPicker } from "./Perf";
import { filterRuns, RunsTable } from "./Runs";

afterEach(() => {
  setTransport(httpTransport);
  try {
    localStorage.clear();
  } catch {
    /* no storage */
  }
});

describe("NameEditor", () => {
  it("adds a name: Enter saves the trimmed value", async () => {
    const save = vi.fn(async () => {});
    render(<NameEditor name={null} onSave={save} />);
    fireEvent.click(screen.getByText("+ Add a name"));
    const input = screen.getByLabelText("Name of this run");
    fireEvent.change(input, { target: { value: "  smaller area  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);
    await waitFor(() => expect(save).toHaveBeenCalledWith("smaller area"));
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("Escape cancels without saving", () => {
    const save = vi.fn(async () => {});
    render(<NameEditor name="baseline" onSave={save} what="batch" />);
    fireEvent.click(screen.getByLabelText("Rename this batch"));
    const input = screen.getByLabelText("Name of this batch");
    fireEvent.change(input, { target: { value: "other" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(save).not.toHaveBeenCalled();
    expect(screen.getByText("baseline")).toBeTruthy();
  });

  it("an unchanged name is not saved again", async () => {
    const save = vi.fn(async () => {});
    render(<NameEditor name="baseline" onSave={save} />);
    fireEvent.click(screen.getByLabelText("Rename this run"));
    fireEvent.blur(screen.getByLabelText("Name of this run"));
    await waitFor(() => expect(screen.queryByLabelText("Name of this run")).toBeNull());
    expect(save).not.toHaveBeenCalled();
  });
});

describe("names in lists", () => {
  it("runs table shows the name and the filter finds it", () => {
    const base = { kind: "synth", adapter: "dc", status: "done", knobs: {}, artifacts: {}, tags: [] };
    const runs = [
      { ...base, id: "r_0001", created_at: 2, experiment: "baseline", name: "before retiming" },
      { ...base, id: "r_0002", created_at: 1, experiment: "baseline" },
    ] as RunSummary[];
    render(<RunsTable runs={runs} selected={new Set()} onToggle={() => {}} />);
    expect(screen.getByText("before retiming")).toBeTruthy();
    expect(filterRuns(runs, { kind: "", status: "", tag: "", text: "retim" }).map((r) => r.id)).toEqual(["r_0001"]);
  });

  it("batch label starts with the name", () => {
    const g = { id: "g_0004", kind: "sim-all", created_at: 0, status: "done", params: { tests: ["a", "b"] }, name: "nightly" } as Group;
    expect(batchLabel(g)).toMatch(/^nightly · g_0004 · .* · 2 tests$/);
    expect(batchLabel({ ...g, name: null })).toMatch(/^g_0004 · /);
  });

  it("mock backend renames runs and batches", async () => {
    const be = new MockBackend("demo");
    setTransport(be, true);
    const [r] = await api.runs({ kind: "synth", limit: 1 });
    expect((await api.renameRun(r.id, "  my   run ")).name).toBe("my run");
    expect((await api.renameRun(r.id, "")).name).toBeNull();
    const [g] = await api.groups("sim-all");
    expect((await api.renameGroup(g.id, "before the fix")).name).toBe("before the fix");
  });
});

describe("TestPicker", () => {
  const suite: Suite = { id: "system", label: "System tests", flow: "verilator", flows: ["verilator"], tests: ["alu_basic", "branch_loop", "mem_stress"], unit: false, golden: true, default: true };

  it("runs only the chosen tests, as one named batch", async () => {
    const be = new MockBackend("demo");
    setTransport(be, true);
    const submit = vi.spyOn(api, "submitJob");
    render(<TestPicker suite={suite} project="demo" onClose={() => {}} onDone={() => {}} />);
    expect(screen.getByText("Run all 3 tests")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("branch_loop"));
    fireEvent.change(screen.getByLabelText("Name for this batch"), { target: { value: "two of them" } });
    fireEvent.click(screen.getByText("Run 2 tests"));
    await waitFor(() => expect(submit).toHaveBeenCalled());
    expect(submit).toHaveBeenCalledWith("sim-all", { flow: "verilator", tests: ["alu_basic", "mem_stress"], name: "two of them" });
    await waitFor(async () => expect((await api.groups("sim-all")).some((g) => g.name === "two of them")).toBe(true));
    const g = (await api.groups("sim-all")).find((x) => x.name === "two of them")!;
    expect(g.params.tests).toEqual(["alu_basic", "mem_stress"]);
    submit.mockRestore();
  });

  it("filter, select none, and the choice is remembered", () => {
    const { unmount } = render(<TestPicker suite={suite} project="demo" onClose={() => {}} onDone={() => {}} />);
    fireEvent.click(screen.getByText("Select none"));
    expect((screen.getByText("Run 0 tests").closest("button") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Filter tests"), { target: { value: "mem" } });
    fireEvent.click(screen.getByText("Select 1 shown"));
    expect(screen.getByText("Run 1 test")).toBeTruthy();
    unmount();
    render(<TestPicker suite={suite} project="demo" onClose={() => {}} onDone={() => {}} />);
    expect(screen.getByText("Run 1 test")).toBeTruthy();
  });
});

describe("synthesis list", () => {
  it("shows headline QoR and sorts by it; default is the order runs were made", async () => {
    const { QOR_COLUMNS } = await import("./Synth");
    const base = { kind: "synth", adapter: "dc", status: "done", knobs: {}, artifacts: {}, tags: [] };
    const runs = [
      { ...base, id: "r_0003", created_at: 3, name: "c", qor: { period: 4000, wns: 50, area_total: 900, cell_count: 30, power_total: 1.5 } },
      { ...base, id: "r_0002", created_at: 2, name: "a", qor: { period: 3000, wns: -20, area_total: 1200, cell_count: 40, power_total: 2.5 } },
      { ...base, id: "r_0001", created_at: 1, status: "running" },
    ] as RunSummary[];
    const { container } = render(<RunsTable runs={runs} selected={new Set()} onToggle={() => {}} extraColumns={QOR_COLUMNS} hide={["git", "dur"]} />);
    const ids = () => Array.from(container.querySelectorAll(".vt-row")).map((r) => r.textContent!.slice(0, 6));
    expect(ids()).toEqual(["r_0003", "r_0002", "r_0001"]);
    expect(container.textContent).toContain("−20 ps");
    expect(container.textContent).not.toMatch(/Duration|Git/);
    const header = (text: string) => Array.from(container.querySelectorAll(".vt-cell.sortable")).find((h) => h.textContent!.includes(text))!;
    fireEvent.click(header("Worst slack"));
    expect(ids()).toEqual(["r_0002", "r_0003", "r_0001"]); // ascending; no result last
    fireEvent.click(header("Cells"));
    fireEvent.click(header("Cells"));
    expect(ids()).toEqual(["r_0002", "r_0003", "r_0001"]); // descending
    fireEvent.click(header("Name"));
    expect(ids()[0]).toBe("r_0002");
  });
});
