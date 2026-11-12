import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { httpTransport, setTransport, type Transport } from "../api/client";
import type { PerfResult, RunSummary } from "../api/types";
import { csvField, fileSlug, toCsv } from "../lib/download";
import PerfView, { ipcOf, PERF_METRICS, perfCsv } from "./Perf";
import { PerfHistory, periodNote } from "./PerfHistory";
import { RunsTable } from "./Runs";
import { REPORTS_COLUMN } from "./Synth";

afterEach(() => {
  setTransport(httpTransport);
  vi.restoreAllMocks();
});

const ROWS: PerfResult[] = [
  { test: "fib", cycles: 2000, instrs: 1000, cpi: 2, stalls: { load_use: 40 }, period: 4600, period_source: "synth:r_0031", tpi: 9200 },
  { test: 'weird, "name"\nhere', cycles: 300, instrs: 600, cpi: 0.5, stalls: {}, period: null, period_source: null, tpi: null },
  { test: "noinstrs", cycles: 10, instrs: null, cpi: null, stalls: {} },
];

describe("CSV", () => {
  it("quotes only what needs quoting and leaves missing values empty", () => {
    expect(csvField("plain")).toBe("plain");
    expect(csvField('a,"b"')).toBe('"a,""b"""');
    expect(csvField("two\nlines")).toBe('"two\nlines"');
    expect([null, undefined, NaN, Infinity].map(csvField)).toEqual(["", "", "", ""]);
    expect(csvField(0)).toBe("0");
    expect(toCsv(["a", "b"], [[1, "x"]])).toBe("a,b\r\n1,x\r\n");
  });

  it("perf table: raw numbers, every column, one row per program", () => {
    const lines = perfCsv(ROWS, ["load_use"]).split("\r\n");
    expect(lines[0]).toBe(
      "program,cycles,instructions,cycles_per_instruction,instructions_per_cycle,clock_period_ps,time_per_instruction_ps,stall_cycles_load_use,clock_period_from",
    );
    expect(lines[1]).toBe("fib,2000,1000,2,0.5,4600,9200,40,synth:r_0031");
    expect(lines[2]).toBe('"weird, ""name""\nhere",300,600,0.5,2,,,,'); // a line break inside a quoted field
    expect(lines[3]).toBe("noinstrs,10,,,,,,,");
    expect(lines).toHaveLength(5); // trailing CRLF
  });

  it("file names stay safe", () => {
    expect(fileSlug("before / retiming!")).toBe("before-retiming");
    expect(fileSlug("../../etc")).toBe("etc");
  });
});

describe("perf metrics", () => {
  it("IPC is instructions over cycles, or 1 / CPI", () => {
    expect(ipcOf(ROWS[0])).toBe(0.5);
    expect(ipcOf({ test: "t", cpi: 4, stalls: {} })).toBe(0.25);
    expect(ipcOf(ROWS[2])).toBeNull();
    expect(PERF_METRICS.ipc.better).toBe("higher");
    expect(PERF_METRICS.tpi.value(ROWS[0])).toBe(9200);
  });

  function serve(rows: PerfResult[]) {
    const t: Transport = {
      request: async (_m, path) => (path.startsWith("/api/perf") ? rows : path.startsWith("/api/groups") ? [] : {}),
    };
    setTransport(t, true);
  }

  it("switches the chart between CPI, IPC and TPI, and downloads the table", async () => {
    serve(ROWS);
    const created: Blob[] = [];
    URL.createObjectURL = vi.fn((b: Blob) => (created.push(b), "blob:x")) as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<PerfView />);
    const cpi = await screen.findByRole("radio", { name: "Cycles / instr." });
    expect(cpi.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText("lower is better")).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Instr. / cycle" }));
    expect(screen.getByRole("radio", { name: "Instr. / cycle" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText("higher is better")).toBeTruthy();
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("instructions per cycle");
    fireEvent.click(screen.getByRole("radio", { name: "Time / instr." }));
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("time per instruction");

    fireEvent.click(screen.getByText("Download CSV"));
    expect(click).toHaveBeenCalledTimes(1);
    const a = click.mock.instances[0] as unknown as HTMLAnchorElement;
    expect(a.download).toBe("perf-latest.csv");
    expect(created[0].type).toBe("text/csv;charset=utf-8");
    const text = await new Promise<string>((ok) => {
      const fr = new FileReader();
      fr.onload = () => ok(String(fr.result));
      fr.readAsText(created[0]);
    });
    expect(text).toContain("fib,2000,1000,2,0.5,4600,9200,40,synth:r_0031");
  });

  it("time per instruction is unavailable until a clock period is known", async () => {
    serve(ROWS.map((r) => ({ ...r, tpi: null, period: null })));
    render(<PerfView />);
    const tpi = await screen.findByRole("radio", { name: "Time / instr." });
    expect((tpi as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect(screen.getByRole("radio", { name: "Cycles / instr." }).getAttribute("aria-checked")).toBe("true"));
  });
});

describe("synthesis report download", () => {
  it("each finished run links to its .txt reports; running ones do not", () => {
    const base = { kind: "synth", adapter: "dc", knobs: {}, artifacts: {}, tags: [] };
    const runs = [
      { ...base, id: "r_0031", created_at: 2, status: "done", name: "my run" },
      { ...base, id: "r_0032", created_at: 3, status: "running" },
    ] as RunSummary[];
    render(<RunsTable runs={runs} selected={new Set()} onToggle={() => {}} extraColumns={[REPORTS_COLUMN]} />);
    const link = screen.getByLabelText("Download the reports of r_0031") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/api/runs/r_0031/reports.txt");
    expect(link.hasAttribute("download")).toBe(true);
    expect(screen.queryByLabelText("Download the reports of r_0032")).toBeNull();
  });
});

describe("clock period source", () => {
  function serve(rows: PerfResult[]) {
    const seen: Array<{ path: string; q: Record<string, unknown> }> = [];
    setTransport({
      request: async (_m, path, q) => (seen.push({ path, q: { ...(q ?? {}) } }), path.startsWith("/api/perf") ? rows : path.startsWith("/api/groups") ? [] : {}),
    }, true);
    return seen;
  }

  // no rows: jsdom has no canvas, so a chart redrawn after a new fetch would crash the test
  it("asks for svkit.toml by default, then the sweep, then a period typed in", async () => {
    const seen = serve([]);
    const perfQ = () => seen.filter((x) => x.path === "/api/perf").at(-1)!.q;
    render(<PerfView />);
    await screen.findByText("No performance data yet");
    expect(perfQ().period_from).toBeUndefined();                       // svkit.toml is the server default

    const from = screen.getByLabelText("Clock period from") as HTMLSelectElement;
    fireEvent.change(from, { target: { value: "sweep" } });
    await waitFor(() => expect(perfQ().period_from).toBe("sweep"));

    fireEvent.change(from, { target: { value: "manual" } });
    await waitFor(() => expect(perfQ()).toMatchObject({ period_from: "manual", period: 1000 }));
    const ps = screen.getByLabelText("Clock period in ps");
    fireEvent.change(ps, { target: { value: "0" } });
    fireEvent.keyDown(ps, { key: "Enter" });                          // not a period: not applied
    expect(ps.getAttribute("aria-invalid")).toBe("true");
    fireEvent.change(ps, { target: { value: "1250" } });
    fireEvent.keyDown(ps, { key: "Enter" });
    await waitFor(() => expect(perfQ()).toMatchObject({ period_from: "manual", period: 1250 }));
    expect(seen.filter((x) => x.path === "/api/perf").every((x) => x.q.period !== 0)).toBe(true);
  });

  it("the history asks for the same period and says where it comes from", async () => {
    const seen = serve([]);
    render(<PerfHistory test="fib" tests={["fib"]} period={{ from: "manual", ps: 1250 }} onTest={() => {}} onClose={() => {}} />);
    await waitFor(() => expect(seen.find((x) => x.path === "/api/perf/history")?.q).toMatchObject({ test: "fib", period_from: "manual", period: 1250 }));
    expect(periodNote({ from: "manual", ps: 1250 })).toContain("you entered");
    expect(periodNote({ from: "sweep" })).toContain("when each run was made");
    expect(periodNote()).toContain("svkit.toml");
  });
});
