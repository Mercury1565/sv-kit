import { afterEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { httpTransport, setTransport, type Transport } from "../api/client";
import type { PerfHistoryPoint } from "../api/types";
import { delta, historyWindow, HISTORY_WINDOW, PerfHistory, pointLabel } from "./PerfHistory";

afterEach(() => setTransport(httpTransport));

const pt = (i: number, extra: Partial<PerfHistoryPoint> = {}): PerfHistoryPoint => ({
  test: "fib", cycles: 1000 + i, instrs: 1000, cpi: (1000 + i) / 1000, stalls: {}, period: 4000, period_source: "synth:r_0001",
  tpi: ((1000 + i) / 1000) * 4000, run_id: `r_${String(i).padStart(4, "0")}`, status: "passed", created_at: 1e9 + i * 3600, ...extra,
});

describe("perf history helpers", () => {
  it("windows of eight, newest first, never past either end", () => {
    expect(HISTORY_WINDOW).toBe(8);
    expect(historyWindow(5, 0)).toEqual([0, 5]);
    expect(historyWindow(14, 0)).toEqual([6, 14]);
    expect(historyWindow(14, 1)).toEqual([0, 8]);
    expect(historyWindow(14, 5)).toEqual([0, 8]);
    expect(historyWindow(0, 0)).toEqual([0, 0]);
  });

  it("labels a bar by batch name, run name, batch id, then run id", () => {
    expect(pointLabel(pt(1, { group_name: "after the fix", run_name: "x", group_id: "g_1" }))).toBe("after the fix");
    expect(pointLabel(pt(1, { run_name: "solo", group_id: "g_1" }))).toBe("solo");
    expect(pointLabel(pt(1, { group_id: "g_1" }))).toBe("g_1");
    expect(pointLabel(pt(1))).toBe("r_0001");
  });

  it("change: up is worse, down is better", () => {
    expect(delta(2, 1)).toEqual({ text: "+100%", tone: "worse" });
    expect(delta(0.5, 1)).toEqual({ text: "−50%", tone: "better" });
    expect(delta(1, 1).text).toBe("±0%");
    expect(delta(null, 1).text).toBe("");
    expect(delta(1, 0).text).toBe("");
  });
});

describe("PerfHistory", () => {
  function serve(points: PerfHistoryPoint[]) {
    const seen: string[] = [];
    const t: Transport = { request: async (_m, path, q) => (seen.push(`${path}?${q?.test}`), path === "/api/perf/history" ? points : []) };
    setTransport(t, true);
    return seen;
  }

  it("shows one row per run, newest eight, and pages to older ones", async () => {
    const seen = serve(Array.from({ length: 11 }, (_, i) => pt(i + 1, i === 10 ? { group_id: "g_0009", group_name: "nightly" } : {})));
    render(<PerfHistory test="fib" tests={["fib", "gcd"]} onTest={() => {}} onClose={() => {}} />);
    await screen.findByText("runs 4–11 of 11");
    expect(seen).toContain("/api/perf/history?fib");
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 8);
    expect(within(table).getByText("nightly")).toBeTruthy();
    expect(within(table).queryByText("r_0001")).toBeNull();
    fireEvent.click(screen.getByLabelText("Older runs"));
    expect(screen.getByText("runs 1–8 of 11")).toBeTruthy();
    expect(within(screen.getByRole("table")).getByText("r_0001")).toBeTruthy();
    expect((screen.getByLabelText("Older runs") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByRole("img").map((e) => e.getAttribute("aria-label"))).toEqual([
      "cycles per instruction of fib over 8 runs",
      "time per instruction (ps) of fib over 8 runs",
    ]);
  });

  it("says so when no run reported numbers, and without a period has no TPI chart", async () => {
    serve([]);
    const { unmount } = render(<PerfHistory test="gcd" tests={["gcd"]} onTest={() => {}} onClose={() => {}} />);
    await screen.findByText(/No run of gcd reported/);
    unmount();
    serve([pt(1, { period: null, tpi: null }), pt(2, { period: null, tpi: null })]);
    render(<PerfHistory test="fib" tests={["fib"]} onTest={() => {}} onClose={() => {}} />);
    await screen.findByText(/No clock period was known/);
    expect(screen.getAllByRole("img")).toHaveLength(1);
  });
});
