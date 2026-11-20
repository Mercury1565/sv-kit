import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { httpTransport, setTransport, type Transport } from "../api/client";
import type { PerfBatch } from "../api/types";
import { batchExportPlan, batchMeansCsv, PerfBatches } from "./PerfBatches";
import { batchMeans } from "../lib/perfMeans";

afterEach(() => setTransport(httpTransport));
// no ECharts here (jsdom has no canvas, and its lazy import could finish after teardown)
vi.mock("../components/EChart", async (orig) => ({ ...(await orig<object>()), EChart: () => null }));

const batch = (i: number, tests: string[], name?: string): PerfBatch => ({
  group_id: `g_${String(i).padStart(4, "0")}`, group_name: name ?? null, created_at: 1e9 + i * 3600, status: "done",
  results: tests.map((t, k) => ({ test: t, run_id: `r_${i}_${k}`, cpi: 1 + k + i / 10, period: 4000, period_source: "svkit.toml", tpi: (1 + k + i / 10) * 4000 })),
});

function serve(batches: PerfBatch[]) {
  const t: Transport = { request: async (_m, path) => (path === "/api/perf/batches" ? batches : []) };
  setTransport(t, true);
}

describe("PerfBatches", () => {
  it("one row per batch with both means, newest eight, paging to older ones", async () => {
    serve(Array.from({ length: 10 }, (_, i) => batch(i + 1, ["a", "b"], i === 9 ? "nightly" : undefined)));
    render(<PerfBatches onClose={() => {}} />);
    await screen.findByText("batches 3–10 of 10", undefined, { timeout: 5000 });
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 8);
    expect(within(table).getByText("nightly")).toBeTruthy();
    // g_0010: CPIs 2 and 3 -> mean 2.5, geomean sqrt(6)
    const row = within(table).getByText("nightly").closest("tr")!;
    expect(within(row).getByText("2.5")).toBeTruthy();
    expect(within(row).getByText("2.449")).toBeTruthy();
    expect(screen.queryByRole("radiogroup")).toBeNull();   // same tests everywhere: nothing to choose
    fireEvent.click(screen.getByLabelText("Older batches"));
    expect(screen.getByText("batches 1–8 of 10")).toBeTruthy();
  });

  it("offers averaging only the tests every batch ran when the batches differ", async () => {
    serve([batch(1, ["a", "b"]), batch(2, ["b", "c"])]);
    render(<PerfBatches onClose={() => {}} />);
    const only = await screen.findByRole("radio", { name: /Only tests in every batch \(1\)/ }, { timeout: 5000 });
    const row = () => within(screen.getByRole("table")).getByText("g_0001").closest("tr")!;
    expect(within(row()).getAllByText("2")).toHaveLength(1);    // two tests averaged
    fireEvent.click(only);
    expect(only.getAttribute("aria-checked")).toBe("true");
    expect(within(row()).getByText("1")).toBeTruthy();          // just "b"
  });

  it("export: four charts per page of batches, and a CSV row per batch", () => {
    const all = Array.from({ length: 12 }, (_, i) => batchMeans(batch(i + 1, ["a"])));
    const plan = batchExportPlan(all);
    expect(plan.map((p) => p.file).slice(0, 4)).toEqual(["cpi-mean-batches-01-08.png", "cpi-geomean-batches-01-08.png", "tpi-mean-batches-01-08.png", "tpi-geomean-batches-01-08.png"]);
    expect(plan).toHaveLength(8);
    expect(batchExportPlan(all.slice(0, 2)).map((p) => p.file)).toEqual(["cpi-mean.png", "cpi-geomean.png", "tpi-mean.png", "tpi-geomean.png"]);
    const csv = batchMeansCsv(all.slice(0, 1), "over each batch's own tests").trim().split(/\r?\n/);
    expect(csv[0]).toContain("cpi_geometric_mean");
    expect(csv[1]).toContain("g_0001");
  });
});
