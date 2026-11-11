// Mock backend for development, tests and break-ui worst cases.
// Enable with ?mock=1 (or VITE_MOCK=1). Pick a dataset with ?data=demo|worst|empty.
// Implements the documented API (ARCHITECTURE §6) including the feature routes other
// tracks are still building, so every view can be exercised without a backend.
import { computeDelta } from "../lib/compare";
import { hasX, normBits } from "../lib/waves";
import { ApiError, type Query, type Transport } from "./client";
import type {
  BlockGraph,
  CompareResult,
  ConeGraph,
  DesignHierarchy,
  Group,
  Job,
  PerfResult,
  Project,
  Run,
  RunSummary,
  ServerEvent,
  SimResult,
  SweepResult,
  SynthResult,
  TimingPath,
  WaveChange,
  WaveDecode,
  WaveScope,
  WaveSignalData,
  WaveVar,
  XTraceNode,
} from "./types";

import type { Dataset } from "./dataset";
export { mockDataset, type Dataset } from "./dataset";

// ------------------------------------------------------------------ rng
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

const pick = <T,>(r: () => number, xs: T[]): T => xs[Math.floor(r() * xs.length)];
const pad4 = (n: number) => String(n).padStart(4, "0");
const NOW = Math.floor(Date.UTC(2026, 10, 2, 15, 0, 0) / 1000);

// ------------------------------------------------------------------ synth results
const LONG_INST =
  "u_core/gen_lanes[12].u_lane/u_fwd_unit/bypass_select_ex_mem_q_reg[1]";

function makePaths(r: () => number, n: number, period: number, wns: number, worst: boolean): TimingPath[] {
  const paths: TimingPath[] = [];
  const cells = ["DFFR_X1_R", "NAND2_X1_R", "NOR2_X2_L", "AOI21_X1_R", "INV_X4_L", "XOR2_X1_R", "MUX2_X1_R", "OAI22_X1_H", "BUF_X8_L"];
  for (let i = 0; i < n; i++) {
    const slack = wns - i * (5 + r() * 30);
    const npts = worst && i === 0 ? 64 : 6 + Math.floor(r() * 9);
    const pts = [];
    let arr = 0;
    const start = worst && i === 0 ? LONG_INST : `u_core/u_exec/u_alu/opa_q_reg[${(i * 7) % 32}]`;
    for (let k = 0; k < npts; k++) {
      const incr = k === 0 ? 0 : 15 + r() * 60;
      arr += incr;
      const inst = k === 0 ? start.replace(/\/[^/]+$/, "") + "/" + start.split("/").pop() : `u_core/u_exec/u_alu/U${1000 + i * 37 + k}`;
      pts.push({
        inst,
        pin: k === 0 ? "CK" : k === npts - 1 ? "D" : pick(r, ["A", "B", "Y", "Z", "A1", "B2"]),
        cell: pick(r, cells),
        incr: Number(incr.toFixed(1)),
        arrival: Number(arr.toFixed(1)),
        rise_fall: (r() > 0.5 ? "r" : "f") as "r" | "f",
        net: `n${4000 + k + i * 13}`,
      });
    }
    paths.push({
      startpoint: start,
      endpoint: worst && i === 1 ? "u_core/u_lsu/gen_store_buffer[7].u_entry/data_q_reg[63]" : `u_core/u_exec/result_q_reg[${(i * 5) % 32}]`,
      path_group: "clk",
      path_type: i === n - 1 ? "min" : "max",
      slack: Number(slack.toFixed(1)),
      required: period,
      arrival: Number(arr.toFixed(1)),
      points: pts,
    });
  }
  return paths;
}

function makeSynth(seed: number, opts: { tool: string; period: number; corner?: string; areaScale?: number; lvt?: number; worst?: boolean }): SynthResult {
  const r = rng(seed);
  const worst = !!opts.worst;
  const wns = worst ? -123456.7 : Number((opts.period * 0.06 - r() * opts.period * 0.1).toFixed(1));
  const area = (worst ? 1.234e9 : 8200) * (opts.areaScale ?? 1) * (0.95 + r() * 0.1);
  const hier: Record<string, number> = { "": area };
  const blocks = ["u_core", "u_core/u_fetch", "u_core/u_decode", "u_core/u_exec", "u_core/u_exec/u_alu", "u_core/u_exec/u_mul", "u_core/u_lsu", "u_mem_ctrl"];
  const fracs = [0.82, 0.12, 0.09, 0.38, 0.14, 0.2, 0.17, 0.15];
  blocks.forEach((b, i) => (hier[b] = area * fracs[i] * (0.9 + r() * 0.2)));
  if (worst) {
    for (let i = 0; i < 2500; i++) {
      hier[`u_core/gen_lanes[${i % 16}].u_lane/u_fwd_unit/gen_bypass[${i}].u_bypass_select_ex_mem`] = (area * 0.001) / (1 + (i % 50));
    }
  }
  const lvt = opts.lvt ?? 0;
  const total = worst ? 98765432 : Math.round(area / 2.3);
  const vt_mix: Record<string, number> = worst
    ? { RVT: 41234567, LVT: 31234567, HVT: 25000000, other: 1296298, "ULVT_with_a_very_long_class_name": 0 }
    : { RVT: Math.round(total * (1 - lvt) * 0.8), LVT: Math.round(total * lvt), HVT: Math.round(total * (1 - lvt) * 0.2) };
  const warnings = worst
    ? Array.from({ length: 400 }, (_, i) => ({
        severity: (i % 7 === 0 ? "error" : "warning") as "error" | "warning",
        code: i % 3 ? "LINT-1" : "OPT-1206",
        text:
          i % 3
            ? `Cell '${LONG_INST}_${i}' does not drive any nets and has been removed during optimization because its output is unloaded (constant propagation).`
            : `Register 'gen_lanes[${i % 16}].u_lane.valid_q' is a constant and will be removed.`,
        file: i % 5 ? "rtl/core/lanes/fwd_unit_with_a_long_descriptive_file_name.sv" : null,
        line: i % 5 ? 100 + i : null,
      }))
    : [
        { severity: "warning" as const, code: "OPT-1206", text: "Register 'u_core/u_decode/imm_q_reg[31]' is a constant and will be removed.", file: null, line: null },
        { severity: "warning" as const, code: "LINT-28", text: "Port 'dbg_en' is not connected to any nets.", file: "rtl/core.sv", line: 42 },
      ];
  return {
    schema_version: 1,
    tool: opts.tool,
    top: "core",
    period: opts.period,
    corner: opts.corner ?? "tt_1p0v_25c",
    wns,
    tns: wns < 0 ? Number((wns * (worst ? 4321 : 7)).toFixed(1)) : 0,
    hold_wns: worst ? null : Number((12 + r() * 20).toFixed(1)),
    hold_tns: worst ? Number.NaN : 0,
    area_total: area,
    area_unit: "um^2",
    area_by_hier: hier,
    cell_count: total,
    cells_by_type: { DFFR_X1_R: Math.round(total * 0.2), NAND2_X1_R: Math.round(total * 0.3), INV_X4_L: Math.round(total * 0.1) },
    vt_mix,
    power: worst ? { dynamic: 123456.789, leakage: null, total: Number.NaN } : { dynamic: 2.4 + r(), leakage: 0.31 + r() * 0.1, total: 2.8 + r() },
    paths: makePaths(r, worst ? 30 : 8, opts.period, wns, worst),
    warnings,
    netlist: "netlist.v",
    extra: worst ? { "dc.compile_ultra.retime": true, note: "tool-specific extra with a long value ".repeat(5) } : {},
  };
}

// ------------------------------------------------------------------ runs
const DEMO_TESTS = ["alu_basic", "branch_loop", "mem_stress", "fib_rec", "hazards_fwd", "x_uninit_mem", "x_no_reset", "x_tb_input"];
const WORST_TESTS = [
  "regression/riscv-tests/rv32ui-p-sltiu_with_extremely_long_descriptive_suffix_for_ci_matrix",
  "a",
  "mem_stress",
  "x_uninit_mem",
  "测试_分支预测",
  "<script>alert(1)</script>",
];

interface MockState {
  dataset: Dataset;
  project: Project;
  runs: Run[];
  groups: Group[];
  jobs: Job[];
  logs: Map<string, string[]>;
  nextRun: number;
  nextGroup: number;
}

function simMetrics(test: string, passed: boolean | null, r: () => number): SimResult {
  return {
    schema_version: 1,
    tool: "verilator",
    test,
    passed,
    reason: passed === false ? (test.startsWith("x_") ? "golden mismatch at line 42 (X in writeback data)" : "golden mismatch at line 17") : null,
    sim_time: 100000,
    waves: "waves.fst",
    divergence: passed === false ? { line: test.startsWith("x_") ? 42 : 17, expected: "@@@ 0x0040 r5=0000001c", actual: "@@@ 0x0040 r5=xxxxxxxx", time: 4175, cycle: 417 } : null,
    perf: { test, cycles: Math.round(2000 + r() * 50000), instrs: Math.round(1500 + r() * 30000), cpi: null, stalls: {} },
    warnings: [],
  };
}

function baseRun(id: number, over: Partial<Run>): Run {
  return {
    id: `r_${pad4(id)}`,
    kind: "sim",
    adapter: "verilator",
    status: "passed",
    created_at: NOW - 3600,
    test: null,
    started_at: NOW - 3600,
    finished_at: NOW - 3500,
    duration_s: 12.3,
    git_sha: "c81a80e4f2d1b7a90d3e",
    git_dirty: false,
    source_hash: "ab12",
    knobs: {},
    experiment: null,
    cache_key: null,
    cached_from: null,
    exit_code: 0,
    metrics: null,
    artifacts: {},
    group_id: null,
    message: null,
    tags: [],
    ...over,
  };
}

function buildState(dataset: Dataset): MockState {
  const st: MockState = {
    dataset,
    project: {
      name: "demo-core",
      root: "/home/user/projects/demo-core",
      top: "core",
      version: "0.1.0.dev0",
      git_sha: "c81a80e4f2d1b7a90d3e",
      git_dirty: true,
      flows: { sim: ["verilator", "vcs"], synth: ["yosys", "dc"], sta: ["opensta"] },
      knobs: ["clock_period", "corner", "max_fanout"],
      experiments: ["baseline", "hi_area", "mixed_vt", "ss_corner"],
      tests: DEMO_TESTS,
      adapters: { verilator: true, vcs: true, yosys: true, dc: true, opensta: true },
    },
    runs: [],
    groups: [],
    jobs: [],
    logs: new Map(),
    nextRun: 1,
    nextGroup: 1,
  };
  if (dataset === "empty") {
    st.project = { ...st.project, name: "new-project", git_sha: null, git_dirty: null, flows: { sim: [], synth: [], sta: [] }, knobs: [], experiments: [], tests: [], adapters: {} };
    return st;
  }
  const r = rng(dataset === "worst" ? 7 : 42);
  const add = (over: Partial<Run>) => {
    const run = baseRun(st.nextRun++, over);
    st.runs.push(run);
    return run;
  };

  if (dataset === "worst") {
    st.project = {
      ...st.project,
      name: "aleksandra_wisniewska_kowalczyk_out_of_order_superscalar_rv64gc_core_v2_final",
      root: "/home/user/very/deep/directory/structure/for/coursework/projects/out_of_order_superscalar",
      git_sha: null,
      git_dirty: null,
      tests: WORST_TESTS,
      experiments: ["baseline", "compile_ultra_with_retiming_and_high_area_effort_ss0p72v125c", "x"],
    };
    // 10,000 runs, mixed kinds and statuses, long names spread across the first rows.
    const statuses = ["passed", "failed", "running", "error", "queued", "cancelled", "done"];
    for (let i = 0; i < 10000; i++) {
      const kind = i % 3 === 0 ? "synth" : i % 11 === 0 ? "sweep-point" : "sim";
      const test = kind === "sim" ? WORST_TESTS[i % WORST_TESTS.length] : null;
      const status = kind === "synth" ? (i % 5 === 0 ? "error" : "done") : statuses[i % statuses.length];
      add({
        kind,
        adapter: kind === "sim" ? (i % 2 ? "vcs" : "verilator") : i % 2 ? "dc" : "yosys+opensta_with_custom_plugin_adapter",
        test,
        status,
        created_at: NOW - (10000 - i) * 97,
        duration_s: i === 1 ? 3 * 86400 + 4000 : i === 2 ? null : r() * 900,
        git_sha: i % 4 === 0 ? null : (hash(String(i)).toString(16) + "0000000000").slice(0, 40),
        git_dirty: i % 3 === 0,
        experiment: kind === "synth" ? st.project.experiments[i % 3] : null,
        tags: i === 0 ? ["nightly", "baseline", "needs-review", "q3", "regression", "perf", "timing", "dogfood", "ta-demo", "customer-feedback-from-enterprise-onboarding", "x", "y", "z", "final"] : i % 13 === 0 ? ["nightly"] : [],
        message: i % 7 === 3 ? "Error: dc_shell-t exited with status 1: license checkout failed for DC-Ultra-Opt (all 2 seats in use by other users on this server)" : null,
        metrics: kind === "sim" ? simMetrics(test!, status === "passed" ? true : status === "failed" ? false : null, r) : makeSynth(i, { tool: "dc", period: 4000, worst: i < 30 }),
        artifacts: { "sim.log": "sim.log", "waves.fst": "waves.fst", "reports/very/deep/path/report_timing_max_paths_100_nworst_10.rpt": "x" },
        cached_from: i % 17 === 0 ? "r_0001" : null,
      });
    }
    const g = { id: "g_0001", kind: "sweep", created_at: NOW - 500, status: "running", params: { tol: 50 }, result: null };
    st.groups.push(g);
    st.nextGroup = 2;
    return st;
  }

  // demo: history of sim runs, a matrix, a sweep
  for (let day = 3; day >= 0; day--) {
    for (const t of DEMO_TESTS) {
      const failing = day === 0 && (t === "hazards_fwd" || t.startsWith("x_"));
      add({
        kind: "sim",
        adapter: "verilator",
        test: t,
        status: failing ? "failed" : "passed",
        created_at: NOW - day * 86400 - DEMO_TESTS.indexOf(t) * 60,
        duration_s: 2 + r() * 20,
        metrics: simMetrics(t, !failing, r),
        artifacts: { "sim.log": "sim.log", "waves.fst": "waves.fst", "program.out": "program.out" },
        exit_code: failing ? 1 : 0,
      });
    }
  }
  st.groups.push({ id: "g_0001", kind: "matrix", created_at: NOW - 7200, status: "done", params: {}, result: null });
  const exps: [string, number, string, number][] = [
    ["baseline", 1, "tt_1p0v_25c", 0],
    ["hi_area", 0.93, "tt_1p0v_25c", 0],
    ["mixed_vt", 1.02, "tt_1p0v_25c", 0.2],
    ["ss_corner", 1.01, "ss_0p9v_125c", 0],
  ];
  exps.forEach(([exp, scale, corner, lvt], i) =>
    add({
      kind: "synth",
      adapter: "dc",
      status: "done",
      experiment: exp,
      group_id: "g_0001",
      created_at: NOW - 7000 + i * 300,
      duration_s: 380 + r() * 400,
      knobs: { clock_period: 4000, corner },
      metrics: makeSynth(100 + i, { tool: "dc", period: 4000, corner, areaScale: scale, lvt }),
      artifacts: { "synth.log": "synth.log", "netlist.v": "netlist.v", "timing.rpt": "timing.rpt", "area.rpt": "area.rpt" },
      tags: i === 0 ? ["baseline"] : [],
    }),
  );
  add({
    kind: "synth",
    adapter: "yosys",
    status: "done",
    experiment: "baseline",
    created_at: NOW - 5000,
    duration_s: 95,
    knobs: { clock_period: 4000 },
    metrics: makeSynth(200, { tool: "yosys+opensta", period: 4000, areaScale: 1.15 }),
    artifacts: { "synth.log": "synth.log", "netlist.json": "netlist.json" },
  });
  // sweep
  st.groups.push({ id: "g_0002", kind: "sweep", created_at: NOW - 3000, status: "done", params: { tol: 50, jobs: 2 }, result: null });
  const periods = [4000, 3600, 3400, 3300, 3250, 3200, 3100, 2900];
  periods.forEach((p, i) => {
    const res = makeSynth(300 + i, { tool: "dc", period: p, areaScale: 1 + (4000 - p) / 6000 });
    // T_min ~ 3250, with one non-monotonic point at 3100 passing
    res.wns = p >= 3250 || p === 3100 ? Number(((p - 3250) * 0.25 + 4 + i).toFixed(1)) : Number(((p - 3250) * 0.6).toFixed(1));
    add({
      kind: "sweep-point",
      adapter: "dc",
      status: "done",
      group_id: "g_0002",
      created_at: NOW - 2900 + i * 120,
      duration_s: 300 + r() * 200,
      knobs: { clock_period: p },
      metrics: res,
      artifacts: { "synth.log": "synth.log", "netlist.v": "netlist.v" },
    });
  });
  st.nextGroup = 3;
  // each day's sim history above is one `sim --all` batch
  for (let day = 3; day >= 0; day--) {
    const id = `g_${pad4(st.nextGroup)}`;
    const dayRuns = st.runs.filter((r) => r.kind === "sim" && !r.group_id && r.created_at <= NOW - day * 86400 && r.created_at > NOW - (day + 1) * 86400);
    if (!dayRuns.length) continue;
    dayRuns.forEach((r) => (r.group_id = id));
    st.groups.push({ id, kind: "sim-all", created_at: Math.min(...dayRuns.map((r) => r.created_at)), status: "done", params: { flow: "verilator", tests: dayRuns.map((r) => r.test) }, result: null });
    st.nextGroup++;
  }
  for (const run of st.runs) {
    if (run.kind === "sim" && run.metrics) {
      const m = run.metrics as SimResult;
      m.perf = m.perf ? { ...m.perf, cpi: m.perf.cycles! / m.perf.instrs! } : null;
    }
  }
  return st;
}

/** Same rules as the server: one line, trimmed, at most 80 characters; empty means no name. */
function cleanName(v: unknown): string | null {
  const s = typeof v === "string" ? v.split(/\s+/).filter(Boolean).join(" ").slice(0, 80).trim() : "";
  return s || null;
}

function summary(r: Run): RunSummary {
  const { metrics: m, ...rest } = r;
  if ((r.kind === "synth" || r.kind === "sweep-point") && m && typeof m === "object") {
    const s = m as Partial<SynthResult>;
    return { ...rest, qor: { period: s.period, wns: s.wns, hold_wns: s.hold_wns, area_total: s.area_total, area_unit: s.area_unit, cell_count: s.cell_count, power_total: s.power?.total } };
  }
  return rest;
}

// ------------------------------------------------------------------ sweep / compare / perf
function sweepOf(st: MockState, gid: string): SweepResult {
  const g = st.groups.find((x) => x.id === gid);
  if (!g || g.kind !== "sweep") throw new ApiError(404, `no sweep ${gid}`, "/api/sweeps");
  const pts = st.runs
    .filter((r) => r.group_id === gid && r.metrics)
    .map((r) => {
      const m = r.metrics as SynthResult;
      const passed = (m.wns ?? 0) >= 0 && (m.hold_wns ?? 0) >= 0;
      return { period: m.period ?? 0, run_id: r.id, wns: m.wns, hold_wns: m.hold_wns, area_total: m.area_total, power_total: m.power.total, passed };
    })
    .sort((a, b) => a.period - b.period);
  const pass = pts.filter((p) => p.passed);
  const tmin = pass.length ? pass[0] : null;
  return { group_id: gid, knob: "clock_period", tol: Number(g.params.tol ?? 50), points: pts, t_min: tmin?.period ?? null, t_min_run_id: tmin?.run_id ?? null, status: g.status === "running" ? "running" : "done" };
}

function compareOf(st: MockState, ids: string[]): CompareResult {
  const runs = ids.map((id) => {
    const r = st.runs.find((x) => x.id === id);
    if (!r) throw new ApiError(404, `no run ${id}`, "/api/compare");
    return r;
  });
  const ms = runs.map((r) => (r.metrics ?? {}) as Partial<SynthResult>);
  const get = (f: (m: Partial<SynthResult>) => number | null | undefined) => ms.map((m) => f(m) ?? null);
  const vtClasses = Array.from(new Set(ms.flatMap((m) => Object.keys(m.vt_mix ?? {})))).sort();
  return {
    run_ids: ids,
    labels: runs.map((r) => r.name ?? r.experiment ?? r.id),
    metrics: [
      computeDelta("wns", "higher", get((m) => m.wns), "ps"),
      computeDelta("tns", "higher", get((m) => m.tns), "ps"),
      computeDelta("hold_wns", "higher", get((m) => m.hold_wns), "ps"),
      computeDelta("area_total", "lower", get((m) => m.area_total), "um^2"),
      computeDelta("power.total", "lower", get((m) => m.power?.total), "mW"),
      computeDelta("power.dynamic", "lower", get((m) => m.power?.dynamic), "mW"),
      computeDelta("power.leakage", "lower", get((m) => m.power?.leakage), "mW"),
      computeDelta("cell_count", "lower", get((m) => m.cell_count), null),
      computeDelta("period", "none", get((m) => m.period), "ps"),
      ...vtClasses.map((c) => computeDelta(`vt_mix.${c}`, "none", get((m) => m.vt_mix?.[c]), null)),
    ],
  };
}

function perfOf(st: MockState, group?: string): PerfResult[] {
  if (st.dataset === "worst") {
    return [
      { test: WORST_TESTS[0], cycles: 98765432101, instrs: 1, cpi: 98765432101, stalls: {}, period: 3250, period_source: "sweep:g_0002", tpi: 3.2e14 },
      { test: "a", cycles: 0, instrs: 0, cpi: null, stalls: {}, period: null, period_source: null, tpi: null },
      { test: "mem_stress", cycles: 1200, instrs: 1000, cpi: 1.2, stalls: { load_use: 150, branch: 50 }, period: Number.NaN, period_source: "synth:r_0003", tpi: null },
      ...Array.from({ length: 300 }, (_, i) => ({ test: `rv32ui-p-test_${i}`, cycles: 1000 + i * 37, instrs: 900 + i * 11, cpi: (1000 + i * 37) / (900 + i * 11), stalls: {}, period: 3250, period_source: "sweep:g_0002", tpi: ((1000 + i * 37) / (900 + i * 11)) * 3250 })),
    ];
  }
  const sw = st.groups.find((g) => g.kind === "sweep");
  const period = sw ? sweepOf(st, sw.id).t_min ?? 4000 : 4000;
  const src = sw ? `sweep:${sw.id}` : "synth:r_0033";
  const seen = new Map<string, PerfResult>();
  for (const r of st.runs) {
    const m = r.metrics as SimResult | null;
    if (group && r.group_id !== group) continue;
    if (r.kind === "sim" && m?.perf) seen.set(r.test!, m.perf);
  }
  return Array.from(seen.values()).map((p) => ({ ...p, period, period_source: src, tpi: p.cpi != null ? p.cpi * period : null }));
}

// ------------------------------------------------------------------ waves
const TIMESCALE = { factor: 1, unit: "ns" };
const T_END = 100000;
const STATE_ENUM = { "0": "IDLE", "1": "FETCH", "2": "DECODE", "3": "EXECUTE", "4": "WRITEBACK_AND_RETIRE_LONG_NAME" };
const OPC_ENUM = { "0": "ADD", "1": "SUB", "2": "LOAD", "3": "STORE", "4": "BRANCH" };

function laneVars(base: string, worst: boolean): WaveVar[] {
  const vs: WaveVar[] = [
    { name: "bypass_select_ex_mem_q", path: `${base}.bypass_select_ex_mem_q`, width: 2, kind: "reg" },
    { name: "fwd_valid", path: `${base}.fwd_valid`, width: 1, kind: "wire" },
    { name: "fwd_data", path: `${base}.fwd_data`, width: 64, kind: "wire" },
  ];
  if (worst) for (let i = 0; i < 2000; i++) vs.push({ name: `dbg_counter_${i}`, path: `${base}.dbg_counter_${i}`, width: 16, kind: "reg" });
  return vs;
}

function scopeOf(st: MockState, path: string): WaveScope {
  const worst = st.dataset === "worst";
  const lanes = worst ? 16 : 4;
  const v = (p: string, name: string, width: number, kind = "wire", type?: string): WaveVar => ({ name, path: `${p}.${name}`, width, kind, type });
  if (path === "") return { path: "", scopes: [{ name: "tb", path: "tb" }], vars: [] };
  if (path === "tb") return { path, scopes: [{ name: "dut", path: "tb.dut" }], vars: [v("tb", "clk", 1, "reg"), v("tb", "rst_n", 1, "reg"), v("tb", "test_done", 1, "reg"), v("tb", "mem_rdata_in", 32, "reg")] };
  if (path === "tb.dut") return { path, scopes: [{ name: "u_core", path: "tb.dut.u_core" }, { name: "u_mem", path: "tb.dut.u_mem" }], vars: [v(path, "clk", 1), v(path, "rst_n", 1), v(path, "imem_addr", 32)] };
  if (path === "tb.dut.u_core")
    return {
      path,
      scopes: Array.from({ length: lanes }, (_, i) => ({ name: `gen_lanes[${i}]`, path: `${path}.gen_lanes[${i}]` })),
      vars: [v(path, "pc", 32, "reg"), v(path, "state", 3, "reg", "state_e"), v(path, "pkt", 41, "wire", "pkt_t"), v(path, "alu_out", 32), v(path, "valid", 1), v(path, "ready", 1), v(path, "stall", 1), v(path, "acc_q", 32, "reg"), v(path, "temp_real", 64, "real")],
    };
  if (path === "tb.dut.u_mem") return { path, scopes: [], vars: [v(path, "rdata", 32), v(path, "addr", 16), v(path, "we", 1)] };
  const m = /^tb\.dut\.u_core\.gen_lanes\[(\d+)\]$/.exec(path);
  if (m) return { path, scopes: [{ name: "u_lane", path: `${path}.u_lane` }], vars: [] };
  const m2 = /^tb\.dut\.u_core\.gen_lanes\[(\d+)\]\.u_lane$/.exec(path);
  if (m2) return { path, scopes: [{ name: "u_fwd_unit", path: `${path}.u_fwd_unit` }], vars: [v(path, "lane_valid", 1)] };
  if (/\.u_fwd_unit$/.test(path)) return { path, scopes: [], vars: laneVars(path, worst) };
  throw new ApiError(404, `no scope ${path}`, "/api/waves/scope");
}

function allVars(st: MockState): WaveVar[] {
  const out: WaveVar[] = [];
  const walk = (p: string) => {
    const s = scopeOf(st, p);
    out.push(...s.vars);
    s.scopes.forEach((c) => walk(c.path));
  };
  walk("");
  return out;
}

function widthOf(path: string): number {
  if (/\.(clk|rst_n|test_done|valid|ready|stall|we|fwd_valid|lane_valid)$/.test(path)) return 1;
  if (path.endsWith(".state")) return 3;
  if (path.endsWith(".pkt")) return 41;
  if (path.endsWith(".bypass_select_ex_mem_q")) return 2;
  if (path.endsWith(".fwd_data")) return 64;
  if (path.endsWith(".addr")) return 16;
  if (/dbg_counter/.test(path)) return 16;
  if (path.endsWith(".temp_real")) return 64;
  return 32;
}

const waveCache = new Map<string, WaveChange[]>();

function bin(n: number, w: number): string {
  return (n >>> 0).toString(2).padStart(w, "0").slice(-w);
}

function genChanges(path: string): WaveChange[] {
  const hit = waveCache.get(path);
  if (hit) return hit;
  const w = widthOf(path);
  const r = rng(hash(path));
  const ch: WaveChange[] = [];
  const P = 10;
  const leaf = path.split(".").pop()!;
  if (leaf === "clk") {
    for (let t = 0; t <= T_END; t += P / 2) ch.push([t, (t / (P / 2)) % 2 ? "1" : "0"]);
  } else if (leaf === "rst_n") {
    ch.push([0, "0"], [35, "1"]);
  } else if (leaf === "pc") {
    let pc = 0;
    ch.push([0, "x".repeat(32)], [35, bin(0, 32)]);
    for (let t = 45; t < T_END; t += P) {
      pc = r() < 0.08 ? Math.floor(r() * 4096) * 4 : pc + 4;
      ch.push([t, bin(pc, 32)]);
    }
  } else if (leaf === "temp_real") {
    for (let t = 0; t < T_END; t += P * 25) ch.push([t, (Math.sin(t / 2000) * 3.3).toFixed(4)]);
  } else if (leaf === "alu_out" || leaf === "acc_q" || leaf === "rdata" || leaf === "mem_rdata_in") {
    ch.push([0, "x".repeat(w)]);
    for (let t = 105; t < T_END; t += P) {
      if (t >= 4165 && t < 4400) {
        if (t < 4175) ch.push([t, leaf === "alu_out" ? "xxxxxxxxxxxxxxxx" + bin(0x1d, 16) : "x".repeat(w)]);
        continue;
      }
      if (leaf === "acc_q" && t > 300 && t < 500) continue;
      if (r() < 0.6) ch.push([t, bin(Math.floor(r() * 2 ** 31), w)]);
    }
    if (leaf === "rdata") ch.push([60000, "z".repeat(w)], [60100, bin(5, w)]);
  } else if (leaf === "state") {
    ch.push([0, "xxx"]);
    for (let t = 35; t < T_END; t += P * (1 + Math.floor(r() * 4))) ch.push([t, bin(Math.floor(r() * 6), 3)]);
  } else if (leaf === "pkt") {
    for (let t = 0; t < T_END; t += P * (1 + Math.floor(r() * 3))) {
      const valid = r() < 0.7 ? "1" : "0";
      ch.push([t, valid + bin(Math.floor(r() * 5), 7) + bin(Math.floor(r() * 32), 5) + bin(Math.floor(r() * 2 ** 28), 28)]);
    }
  } else if (w === 1) {
    ch.push([0, "x"]);
    let v = 0;
    for (let t = 35; t < T_END; t += P * (1 + Math.floor(r() * 6))) {
      v ^= 1;
      ch.push([t, String(v)]);
    }
    if (leaf === "ready") ch.push([50000, "z"], [50200, "1"]);
  } else {
    for (let t = 0; t < T_END; t += P * (1 + Math.floor(r() * 12))) ch.push([t, bin(Math.floor(r() * 2 ** Math.min(31, w)), w)]);
  }
  ch.sort((a, b) => a[0] - b[0]);
  // dedupe same-time, keep last
  const out: WaveChange[] = [];
  for (const c of ch) {
    if (out.length && out[out.length - 1][0] === c[0]) out[out.length - 1] = c;
    else out.push(c);
  }
  waveCache.set(path, out);
  return out;
}

function lowerBound(ch: WaveChange[], t: number): number {
  let lo = 0;
  let hi = ch.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (ch[m][0] < t) lo = m + 1;
    else hi = m;
  }
  return lo;
}

/** Downsample like the backend: buckets with >1 change become "*" (dense) followed by the settled value. */
export function downsample(ch: WaveChange[], t0: number, t1: number, maxChanges: number, init: string): WaveSignalData & { width: number } {
  const i0 = lowerBound(ch, t0 + 1e-9);
  const i1 = lowerBound(ch, t1 + 1e-9);
  const raw = ch.slice(i0, i1);
  if (raw.length <= maxChanges) return { width: 0, init, changes: raw, truncated: false };
  const bucket = (t1 - t0) / Math.max(1, maxChanges);
  const out: WaveChange[] = [];
  let i = 0;
  while (i < raw.length) {
    const b = Math.floor((raw[i][0] - t0) / bucket);
    let j = i;
    while (j + 1 < raw.length && Math.floor((raw[j + 1][0] - t0) / bucket) === b) j++;
    if (j === i) out.push(raw[i]);
    else {
      out.push([raw[i][0], "*"]);
      out.push([raw[j][0], raw[j][1]]);
    }
    i = j + 1;
  }
  return { width: 0, init, changes: out, truncated: true };
}

function decodeOf(path: string): WaveDecode {
  if (path.endsWith(".state")) return { kind: "enum", enum: STATE_ENUM };
  if (path.endsWith(".pkt"))
    return {
      kind: "struct",
      fields: [
        { name: "valid", msb: 40, lsb: 40 },
        { name: "opcode", msb: 39, lsb: 33, enum: OPC_ENUM },
        { name: "rd", msb: 32, lsb: 28 },
        { name: "imm_with_a_long_struct_field_name", msb: 27, lsb: 0 },
      ],
    };
  return { kind: "plain" };
}

// ------------------------------------------------------------------ design / xtrace / source
function hierarchyOf(): DesignHierarchy {
  return {
    top: "core",
    modules: {
      core: { ports: [{ name: "clk", dir: "input", width: 1 }, { name: "rst_n", dir: "input", width: 1 }, { name: "imem_addr", dir: "output", width: 32 }], instances: [{ name: "u_fetch", module: "fetch" }, { name: "u_decode", module: "decode" }, { name: "u_exec", module: "exec" }, { name: "u_lsu", module: "lsu" }], file: "rtl/core.sv", line: 1 },
      fetch: { ports: [], instances: [], file: "rtl/fetch.sv", line: 1 },
      decode: { ports: [], instances: [], file: "rtl/decode.sv", line: 1 },
      exec: { ports: [], instances: [{ name: "u_alu", module: "alu" }, { name: "u_mul", module: "mul" }], file: "rtl/exec.sv", line: 1 },
      lsu: { ports: [], instances: [], file: "rtl/lsu.sv", line: 1 },
      alu: { ports: [], instances: [], file: "rtl/alu.sv", line: 1 },
      mul: { ports: [], instances: [], file: "rtl/mul.sv", line: 1 },
    },
  };
}

function blockOf(st: MockState, module: string): BlockGraph {
  const h = hierarchyOf();
  const mod = h.modules[module];
  if (!mod) throw new ApiError(404, `no module ${module}`, "/api/design/block");
  const p = (name: string, dir: string, width = 1) => ({ name, dir, width });
  if (module === "core") {
    const lanes = st.dataset === "worst" ? 24 : 0;
    return {
      nodes: [
        { id: "u_fetch", label: "u_fetch", module: "fetch", ports: [p("clk", "input"), p("redirect_pc", "input", 32), p("inst", "output", 32), p("pc", "output", 32)] },
        { id: "u_decode", label: "u_decode", module: "decode", ports: [p("inst", "input", 32), p("pc", "input", 32), p("uop", "output", 96)] },
        { id: "u_exec", label: "u_exec", module: "exec", ports: [p("uop", "input", 96), p("redirect_pc", "output", 32), p("lsu_req", "output", 70)] },
        { id: "u_lsu", label: "u_lsu", module: "lsu", ports: [p("lsu_req", "input", 70), p("wb_data", "output", 32)] },
        ...Array.from({ length: lanes }, (_, i) => ({ id: `gen_lanes[${i}].u_lane`, label: `gen_lanes[${i}].u_lane_with_forwarding_and_bypass_network`, module: "lane", ports: [p("in", "input", 64), p("out", "output", 64)] })),
      ],
      edges: [
        { src: "u_fetch.inst", dst: "u_decode.inst", width: 32, net: "if_id_inst" },
        { src: "u_fetch.pc", dst: "u_decode.pc", width: 32, net: "if_id_pc" },
        { src: "u_decode.uop", dst: "u_exec.uop", width: 96, net: "id_ex_uop" },
        { src: "u_exec.redirect_pc", dst: "u_fetch.redirect_pc", width: 32, net: "ex_redirect_pc" },
        { src: "u_exec.lsu_req", dst: "u_lsu.lsu_req", width: 70, net: "ex_mem_req" },
        ...Array.from({ length: lanes }, (_, i) => ({ src: `u_exec.lsu_req`, dst: `gen_lanes[${i}].u_lane.in`, width: 64, net: `lane_in_${i}` })),
      ],
    };
  }
  if (module === "exec")
    return {
      nodes: [
        { id: "u_alu", label: "u_alu", module: "alu", ports: [p("a", "input", 32), p("b", "input", 32), p("y", "output", 32)] },
        { id: "u_mul", label: "u_mul", module: "mul", ports: [p("a", "input", 32), p("b", "input", 32), p("p", "output", 64)] },
      ],
      edges: [{ src: "u_alu.y", dst: "u_mul.a", width: 32, net: "alu_to_mul" }],
    };
  return { nodes: [], edges: [] };
}

function coneOf(run: string, path?: string | number): ConeGraph {
  const onPath = path !== undefined && path !== "";
  const nodes = [
    { id: "opa_q_reg_3_", cell: "DFFR_X1_R", pins: ["D", "CK", "Q"], on_path: onPath, delay: 120.4 },
    { id: "U1201", cell: "NAND2_X1_R", pins: ["A", "B", "Y"], on_path: onPath, delay: 32.1 },
    { id: "U1202", cell: "INV_X4_L", pins: ["A", "Y"], on_path: false, delay: 11.0 },
    { id: "U1203", cell: "AOI21_X1_R", pins: ["A1", "A2", "B", "Y"], on_path: onPath, delay: 48.9 },
    { id: "U1204", cell: "XOR2_X1_R", pins: ["A", "B", "Y"], on_path: onPath, delay: 61.2 },
    { id: "U1205", cell: "MUX2_X1_R", pins: ["A", "B", "S", "Y"], on_path: false, delay: 40.0 },
    { id: "result_q_reg_7_", cell: "DFFR_X1_R", pins: ["D", "CK", "Q"], on_path: onPath, delay: null },
  ];
  const edges = [
    { src: "opa_q_reg_3_", dst: "U1201", net: "opa_q[3]", on_path: onPath },
    { src: "U1202", dst: "U1201", net: "n12", on_path: false },
    { src: "U1201", dst: "U1203", net: "n13", on_path: onPath },
    { src: "U1205", dst: "U1203", net: "n14", on_path: false },
    { src: "U1203", dst: "U1204", net: "n15", on_path: onPath },
    { src: "U1204", dst: "result_q_reg_7_", net: "result_d[7]", on_path: onPath },
  ];
  return { nodes, edges, unresolved: run.endsWith("9") ? ["u_core/u_exec/U9999/A"] : [] };
}

function xtraceOf(signal: string, time: number, worst: boolean): XTraceNode {
  const root: XTraceNode = {
    signal,
    time,
    value: "32'hxxxx001d",
    driver: { kind: "assign", file: "rtl/core.sv", line: 88, text: "assign wb_data = sel_mem ? mem_rdata : alu_out;" },
    children: [
      {
        signal: "tb.dut.u_core.alu_out",
        time,
        value: "32'hxxxx001d",
        driver: { kind: "always_comb", file: "rtl/alu.sv", line: 24, text: "result = opa + opb;" },
        children: [
          {
            signal: "tb.dut.u_core.acc_q",
            time: time - 10,
            value: "32'hxxxxxxxx",
            driver: { kind: "always_ff", file: "rtl/alu.sv", line: 40, text: "if (en) acc_q <= acc_d;" },
            cause: "no_reset",
            note: "acc_q has no reset branch and was never assigned before t=4165",
            children: [],
          },
        ],
      },
      {
        signal: "tb.dut.u_mem.rdata",
        time,
        value: "32'hxxxxxxxx",
        driver: { kind: "always_ff", file: "rtl/mem.sv", line: 31, text: "rdata <= mem[addr];" },
        children: [
          { signal: "tb.dut.u_mem.mem[5]", time: time - 10, value: "32'hxxxxxxxx", driver: { kind: "initial", file: "rtl/mem.sv", line: 12, text: null }, cause: "uninit_memory", note: "mem[5] read at t=4165 but never written", children: [] },
        ],
      },
      {
        signal: "tb.mem_rdata_in",
        time,
        value: "32'hzzzzzzzz",
        driver: { kind: "input", file: "tb/tb.sv", line: 7, text: "logic [31:0] mem_rdata_in; // driven by testbench" },
        cause: "external_input",
        note: "X/Z enters from the testbench port mem_rdata_in",
        children: [],
      },
    ],
  };
  if (worst) {
    // deep chain with long names and a bound-hit leaf
    let node = root.children[0];
    for (let i = 0; i < 25; i++) {
      const c: XTraceNode = {
        signal: `tb.dut.u_core.gen_lanes[${i % 16}].u_lane.u_fwd_unit.bypass_select_ex_mem_q_stage_${i}`,
        time: time - 10 * (i + 2),
        value: "64'bxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        driver: { kind: "always_ff", file: "rtl/core/lanes/fwd_unit_with_a_long_descriptive_file_name.sv", line: 1000 + i, text: "bypass_select_ex_mem_q <= (ex_mem_rd == id_ex_rs1 && ex_mem_wr_en && !ex_mem_is_load) ? 2'b01 : 2'b00;" },
        cause: i === 24 ? "unknown" : null,
        note: i === 24 ? "depth bound (25) reached" : null,
        children: [],
      };
      node.children.push(c);
      node = c;
    }
  }
  return root;
}

function sourceOf(file: string, line: number, context: number) {
  const n = 400;
  const lines = Array.from({ length: n }, (_, i) => {
    const k = i + 1;
    if (k === 1) return `// ${file} (mock source)`;
    if (k === 2) return "module core #(parameter int W = 32) (input logic clk, input logic rst_n);";
    if (k % 17 === 0) return `  logic [W-1:0] acc_q, alu_out, pc, wb_data; // signals`;
    if (k % 11 === 0) return "  always_ff @(posedge clk) if (en) acc_q <= acc_d;";
    if (k % 7 === 0) return "  assign wb_data = sel_mem ? mem_rdata : alu_out;";
    return k % 3 ? "" : `  // line ${k}: ${"long comment text ".repeat(k % 5)}`;
  });
  const start = Math.max(1, line - context);
  return { file, start, lines: lines.slice(start - 1, line + context) };
}

// ------------------------------------------------------------------ transport
export class MockBackend implements Transport {
  st: MockState;
  latency: number;
  emit: (ev: ServerEvent) => void;

  constructor(dataset: Dataset = "demo", opts: { latency?: number; emit?: (ev: ServerEvent) => void } = {}) {
    this.st = buildState(dataset);
    this.latency = opts.latency ?? 0;
    this.emit = opts.emit ?? (() => {});
  }

  async request(method: string, path: string, query: Query = {}, body?: unknown): Promise<unknown> {
    if (this.latency) await new Promise((r) => setTimeout(r, this.latency * (0.5 + Math.random())));
    const res = this.route(method, path, query, (body ?? {}) as Record<string, unknown>);
    return JSON.parse(JSON.stringify(res, (_k, v) => (typeof v === "number" && !Number.isFinite(v) ? null : v)));
  }

  private run(id: string): Run {
    const r = this.st.runs.find((x) => x.id === id);
    if (!r) throw new ApiError(404, `no run ${id}`, `/api/runs/${id}`);
    return r;
  }

  private route(method: string, path: string, q: Query, body: Record<string, unknown>): unknown {
    const st = this.st;
    const s = (k: string) => (q[k] === undefined || q[k] === null ? undefined : String(q[k]));
    let m: RegExpExecArray | null;
    if (path === "/api/project") return st.project;
    if (path === "/api/runs") {
      let rs = st.runs.slice().reverse();
      if (s("kind")) rs = rs.filter((r) => r.kind === s("kind"));
      if (s("status")) rs = rs.filter((r) => r.status === s("status"));
      if (s("group")) rs = rs.filter((r) => r.group_id === s("group"));
      if (s("test")) rs = rs.filter((r) => r.test === s("test"));
      if (s("tag")) rs = rs.filter((r) => r.tags.includes(s("tag")!));
      const off = Number(s("offset") ?? 0);
      const lim = Math.min(5000, Number(s("limit") ?? 200));
      return rs.slice(off, off + lim).map(summary);
    }
    if ((m = /^\/api\/runs\/([^/]+)\/log$/.exec(path))) {
      const r = this.run(decodeURIComponent(m[1]));
      const lines =
        st.logs.get(r.id) ??
        (st.dataset === "worst"
          ? Array.from({ length: 2000 }, (_, i) => `[${i}] %Warning-WIDTH: rtl/core/lanes/fwd_unit_with_a_long_descriptive_file_name.sv:${i}: Operator ASSIGNW expects 64 bits on the Assign RHS, but Assign RHS's VARREF 'bypass_select_ex_mem_q' generates 2 bits.`)
          : [`svkit: run ${r.id} (${r.kind}, ${r.adapter})`, "$ make sim TEST=" + (r.test ?? ""), "verilator --binary -j 4 ...", "- V e r i l a t i o n   R e p o r t", r.status === "failed" ? "@@@ golden mismatch at line 42" : "@@@ PASSED", "svkit: done"]);
      return { lines: lines.slice(-Number(s("tail") ?? 500)) };
    }
    if ((m = /^\/api\/runs\/([^/]+)\/divergence$/.exec(path))) {
      const r = this.run(decodeURIComponent(m[1]));
      const d = (r.metrics as SimResult | null)?.divergence;
      if (!d) throw new ApiError(404, "no divergence for this run", path);
      return d;
    }
    if ((m = /^\/api\/runs\/([^/]+)\/tags(?:\/(.+))?$/.exec(path))) {
      const r = this.run(decodeURIComponent(m[1]));
      if (method === "POST") {
        const tag = String(body.tag ?? "").trim();
        if (!tag) throw new ApiError(400, "tag required", path);
        if (!r.tags.includes(tag)) r.tags = [...r.tags, tag].sort();
      } else if (method === "DELETE" && m[2]) {
        const t = decodeURIComponent(m[2]);
        r.tags = r.tags.filter((x) => x !== t);
      }
      return r;
    }
    if ((m = /^\/api\/runs\/([^/]+)$/.exec(path))) {
      const r = this.run(decodeURIComponent(m[1]));
      if (method === "PATCH") {
        r.name = cleanName(body.name);
        this.emit({ type: "run.updated", run: summary(r) });
      }
      return r;
    }
    if (path === "/api/groups") return st.groups.filter((g) => !s("kind") || g.kind === s("kind"));
    if ((m = /^\/api\/groups\/([^/]+)$/.exec(path))) {
      const g = st.groups.find((x) => x.id === m![1]);
      if (!g) throw new ApiError(404, `no group ${m[1]}`, path);
      if (method === "PATCH") {
        g.name = cleanName(body.name);
        this.emit({ type: "group.updated", group_id: g.id, status: g.status });
        return g;
      }
      return { ...g, runs: st.runs.filter((r) => r.group_id === g.id).map(summary) };
    }
    if (path === "/api/compare") return compareOf(st, (s("ids") ?? "").split(",").filter(Boolean));
    if ((m = /^\/api\/sweeps\/([^/]+)$/.exec(path))) return sweepOf(st, m[1]);
    if (path === "/api/perf") return perfOf(st, s("group"));
    if (path === "/api/jobs" && method === "GET") return st.jobs;
    if (path === "/api/jobs" && method === "POST") return this.submit(String(body.kind), (body.args ?? {}) as Record<string, unknown>);
    if ((m = /^\/api\/waves\/([^/]+)\/(\w+)$/.exec(path))) return this.waves(m[2], q, body, path);
    if (path === "/api/design/hierarchy") return hierarchyOf();
    if (path === "/api/design/block") return blockOf(st, s("module") ?? "core");
    if (path === "/api/design/cone") return coneOf(s("run") ?? "", s("path"));
    if (path === "/api/xtrace") {
      if (!s("signal")) throw new ApiError(400, "signal required", path);
      return xtraceOf(s("signal")!, Number(s("time") ?? 0), st.dataset === "worst");
    }
    if (path === "/api/source") return sourceOf(s("file") ?? "", Number(s("line") ?? 1), Number(s("context") ?? 20));
    throw new ApiError(404, "Not Found", path);
  }

  private waves(op: string, q: Query, body: Record<string, unknown>, path: string): unknown {
    const st = this.st;
    if (st.dataset === "empty") throw new ApiError(404, "run has no waveform", path);
    const s = (k: string) => (q[k] === undefined || q[k] === null ? "" : String(q[k]));
    switch (op) {
      case "meta":
        return { file: "waves.fst", format: "fst", timescale: TIMESCALE, t_start: 0, t_end: T_END };
      case "scope":
        return scopeOf(st, s("path"));
      case "search": {
        const needle = s("q").toLowerCase();
        const lim = Number(s("limit") || 200);
        return allVars(st)
          .filter((v) => v.path.toLowerCase().includes(needle))
          .slice(0, lim)
          .map((v) => ({ path: v.path, width: v.width, kind: v.kind }));
      }
      case "decode":
        return decodeOf(s("signal"));
      case "next": {
        const ch = genChanges(s("signal"));
        const t = Number(s("t"));
        const dir = Number(s("dir") || 1);
        const want = s("what") === "x" ? (v: string) => hasX(v) || /z/.test(v) : () => true;
        if (dir > 0) {
          for (let i = lowerBound(ch, t + 1e-9); i < ch.length; i++) if (ch[i][0] > t && want(ch[i][1])) return { t: ch[i][0] };
        } else {
          for (let i = lowerBound(ch, t) - 1; i >= 0; i--) if (ch[i][0] < t && want(ch[i][1])) return { t: ch[i][0] };
        }
        return { t: null };
      }
      case "data": {
        const sigs = (body.signals as string[]) ?? [];
        const t0 = Number(body.t0 ?? 0);
        const t1 = Number(body.t1 ?? T_END);
        const maxc = Number(body.max_changes ?? 2000);
        const out: Record<string, WaveSignalData> = {};
        for (const p of sigs) {
          const ch = genChanges(p);
          const w = widthOf(p);
          const i = lowerBound(ch, t0 + 1e-9) - 1;
          const init = i >= 0 ? ch[i][1] : normBits("x", w);
          const d = downsample(ch, t0, t1, maxc, init);
          out[p] = { ...d, width: w };
        }
        return { signals: out };
      }
    }
    throw new ApiError(404, "Not Found", path);
  }

  private submit(kind: string, args: Record<string, unknown>) {
    const st = this.st;
    const job: Job = { job_id: `j_${(hash(kind + Date.now()) % 1e8).toString(16)}`, kind, status: "running", run_ids: [], group_id: null, args };
    st.jobs.push(job);
    this.emit({ type: "job.updated", job: { ...job } });
    if (kind === "sim-all") return this.submitBatch(job, args);
    const run = baseRun(st.nextRun++, {
      kind: kind === "sim" || kind === "sim-all" ? "sim" : "synth",
      adapter: kind.startsWith("sim") ? "verilator" : "yosys",
      test: kind === "sim" ? String(args.test ?? "alu_basic") : kind === "sim-all" ? "alu_basic" : null,
      status: "running",
      created_at: Date.now() / 1000,
      experiment: (args.exp as string) ?? null,
      duration_s: null,
      name: kind === "matrix" ? null : cleanName(args.name),
    });
    st.runs.push(run);
    job.run_ids = [run.id];
    st.logs.set(run.id, []);
    setTimeout(() => this.emit({ type: "run.created", run: summary(run) }), 50);
    let n = 0;
    const timer = setInterval(() => {
      const line = `[mock] step ${++n} of ${kind}`;
      st.logs.get(run.id)!.push(line);
      this.emit({ type: "run.log", run_id: run.id, line });
      if (n >= 6) {
        clearInterval(timer);
        run.status = kind.startsWith("sim") ? "passed" : "done";
        run.duration_s = 3;
        run.metrics = kind.startsWith("sim") ? simMetrics(run.test ?? "t", true, rng(1)) : makeSynth(n, { tool: "yosys+opensta", period: 4000 });
        job.status = "done";
        this.emit({ type: "run.status", run_id: run.id, status: run.status });
        this.emit({ type: "job.updated", job: { ...job } });
      }
    }, 400);
    return { job_id: job.job_id };
  }

  /** sim-all: one batch with one run per chosen test (all tests when none are chosen). */
  private submitBatch(job: Job, args: Record<string, unknown>) {
    const st = this.st;
    const all = st.project.tests;
    const tests = Array.isArray(args.tests) && args.tests.length ? (args.tests as string[]) : all;
    const unknown = tests.filter((t) => !all.includes(t));
    if (unknown.length) {
      job.status = "error";
      job.error = `ConfigError: unknown test(s): ${unknown.join(", ")}`;
      setTimeout(() => this.emit({ type: "job.updated", job: { ...job } }), 50);
      return { job_id: job.job_id };
    }
    const g: Group = { id: `g_${pad4(st.nextGroup++)}`, kind: "sim-all", created_at: Date.now() / 1000, status: "running", params: { flow: "verilator", tests, subset: tests.length < all.length }, result: null, name: cleanName(args.name) };
    st.groups.push(g);
    job.group_id = g.id;
    this.emit({ type: "group.updated", group_id: g.id, status: g.status });
    const runs = tests.map((test) => {
      const r = baseRun(st.nextRun++, { kind: "sim", adapter: "verilator", test, status: "running", created_at: Date.now() / 1000, duration_s: null, group_id: g.id });
      st.runs.push(r);
      return r;
    });
    job.run_ids = runs.map((r) => r.id);
    setTimeout(() => runs.forEach((r) => this.emit({ type: "run.created", run: summary(r) })), 50);
    setTimeout(() => {
      runs.forEach((r, i) => {
        r.status = "passed";
        r.duration_s = 2 + i;
        const m = simMetrics(r.test!, true, rng(hash(r.id)));
        if (m.perf) m.perf.cpi = m.perf.cycles! / m.perf.instrs!;
        r.metrics = m;
        this.emit({ type: "run.status", run_id: r.id, status: r.status });
      });
      g.status = "done";
      job.status = "done";
      this.emit({ type: "group.updated", group_id: g.id, status: g.status });
      this.emit({ type: "job.updated", job: { ...job } });
    }, 1200);
    return { job_id: job.job_id };
  }
}
