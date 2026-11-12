import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { httpTransport, setTransport, type Transport } from "../api/client";
import type { PerfResult, RunSummary } from "../api/types";
import { csvField, fileSlug, toCsv } from "../lib/download";
import PerfView, { ipcOf, PERF_METRICS, perfCsv } from "./Perf";
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
      request: async (_m, path) =>
        path === "/api/perf/period" ? { from: "toml", period: 4600, source: "svkit.toml" } : path.startsWith("/api/perf") ? rows : path.startsWith("/api/groups") ? [] : {},
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

describe("clock period for new runs", () => {
  // no rows: jsdom has no canvas, so a chart redrawn after a new fetch would crash the test
  function serve() {
    let state: Record<string, unknown> = { from: "toml", period: 2000, source: "svkit.toml" };
    const seen: Array<{ method: string; path: string; body?: unknown }> = [];
    setTransport({
      request: async (method, path, _q, body) => {
        seen.push({ method, path, body });
        if (path === "/api/perf/period") {
          if (method === "PUT") {
            const b = body as { from: string; ps?: number };
            if (b.from === "manual" && !(Number(b.ps) > 0)) throw new Error("needs a value in ps greater than 0");
            state = { ...b, period: b.from === "manual" ? b.ps : b.from === "sweep" ? 900 : 2000, source: b.from === "manual" ? "manual" : b.from === "sweep" ? "sweep:g_0001" : "svkit.toml" };
          }
          return state;
        }
        return path.startsWith("/api/perf") || path.startsWith("/api/groups") ? [] : {};
      },
    }, true);
    return seen;
  }

  it("shows the project setting and saves changes to it, without refetching the finished runs", async () => {
    const seen = serve();
    const puts = () => seen.filter((x) => x.method === "PUT").map((x) => x.body);
    render(<PerfView />);
    await screen.findByText("No performance data yet");
    const from = (await screen.findByLabelText("Clock period for new runs")) as HTMLSelectElement;
    expect(from.value).toBe("toml");
    expect(screen.getByText("new runs get 2,000 ps (svkit.toml)")).toBeTruthy();
    const tableFetches = seen.filter((x) => x.path === "/api/perf").length;

    fireEvent.change(from, { target: { value: "sweep" } });
    await screen.findByText("new runs get 900 ps (sweep:g_0001)");
    expect(puts()).toEqual([{ from: "sweep" }]);

    fireEvent.change(from, { target: { value: "manual" } });              // starts from the period of now
    await waitFor(() => expect(puts().at(-1)).toEqual({ from: "manual", ps: 900 }));
    const ps = (await screen.findByLabelText("Clock period in ps")) as HTMLInputElement;
    expect(ps.value).toBe("900");
    fireEvent.change(ps, { target: { value: "0" } });
    fireEvent.keyDown(ps, { key: "Enter" });                              // not a period: not sent
    expect(ps.getAttribute("aria-invalid")).toBe("true");
    fireEvent.change(ps, { target: { value: "1250" } });
    fireEvent.keyDown(ps, { key: "Enter" });
    await waitFor(() => expect(puts().at(-1)).toEqual({ from: "manual", ps: 1250 }));
    expect(puts()).toHaveLength(3);
    expect(seen.filter((x) => x.path === "/api/perf").length).toBe(tableFetches);   // finished runs keep theirs
  });
});
