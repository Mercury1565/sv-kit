/**
 * Plain-language names and explanations for engineering terms shown in the dashboard.
 * Labels use full words; the abbreviation engineers know (WNS, CPI, ...) appears only in the
 * explanation behind the (i) button, so newcomers and experts both find their way.
 */
export interface Term {
  label: string;      // what the UI shows
  explain: string;    // what the (i) shows
  abbr?: string;      // the usual abbreviation, mentioned in the explanation
}

export const GLOSSARY = {
  setup_slack: {
    label: "Worst setup slack",
    abbr: "WNS",
    explain: "Time to spare on the slowest path within one clock cycle. Positive: the design meets this clock. Negative: that path arrives this many ps too late, so the clock must be slower or the logic faster.",
  },
  setup_tns: {
    label: "Total setup violation",
    abbr: "TNS",
    explain: "The lateness of every failing path added together. 0 means no path fails. It shows how widespread the timing problems are, not just the worst one.",
  },
  hold_slack: {
    label: "Worst hold slack",
    abbr: "hold WNS",
    explain: "Whether data changes too soon after a clock edge on the fastest path. Negative is a hold violation. It does not depend on the clock period, so a slower clock does not fix it.",
  },
  hold_tns: {
    label: "Total hold violation",
    abbr: "hold TNS",
    explain: "The hold violations of every failing path added together. 0 means none.",
  },
  slack: {
    label: "Slack",
    explain: "Time to spare on a path: when the data must arrive minus when it actually arrives. Negative means the path is too slow for the clock.",
  },
  timing_path: {
    label: "Slowest paths",
    explain: "The routes through the logic that take longest from one register (or input) to the next. The first one limits how fast the design can be clocked.",
  },
  t_min: {
    label: "Fastest passing clock",
    abbr: "T_min",
    explain: "The shortest clock period (highest frequency) at which the synthesized design still meets timing, found by the clock sweep.",
  },
  sweep_tol: {
    label: "Precision",
    explain: "The clock sweep stops once it has narrowed the fastest passing clock down to within this many picoseconds. Smaller is more exact but takes more synthesis runs.",
  },
  clock_period: {
    label: "Clock period",
    explain: "Time between two rising clock edges. Frequency = 1 / period (1000 ps = 1 GHz).",
  },
  cpi: {
    label: "Cycles per instruction",
    abbr: "CPI",
    explain: "Average clock cycles each instruction takes: cycles ÷ instructions. Lower is better; 1.0 means one instruction finishes every cycle.",
  },
  tpi: {
    label: "Time per instruction",
    abbr: "TPI",
    explain: "Average time each instruction takes: cycles per instruction × clock period. It combines how efficient the pipeline is with how fast it can be clocked. Lower is better.",
  },
  vt_mix: {
    label: "Transistor speed mix",
    abbr: "Vt mix",
    explain: "How many cells use each threshold-voltage (Vt) variant. Low-Vt cells switch faster but leak more power; regular- and high-Vt cells are slower and leak less.",
  },
  corner: {
    label: "Operating conditions",
    abbr: "corner",
    explain: "The manufacturing and operating conditions the cell library models, e.g. TT (typical) or SS (slow silicon, low voltage: the worst case for speed).",
  },
  power_split: {
    label: "Switching / leakage power",
    explain: "Switching (dynamic) power is used when signals change value; leakage power is drawn all the time, even when idle. Both in milliwatts.",
  },
  area: {
    label: "Area",
    explain: "Total cell area in the library's unit (usually square micrometres).",
  },
  divergence: {
    label: "First mismatch",
    abbr: "divergence",
    explain: "The first line where the test's output differs from the expected output. svkit opens the waveform at the clock cycle that printed it.",
  },
  gate_cone: {
    label: "Gate cone",
    explain: "The logic gates that drive a signal, traced backwards from it, with the slowest (critical) path highlighted.",
  },
  knobs: {
    label: "Settings",
    abbr: "knobs",
    explain: "Values svkit changed for this run, such as the clock period or the library corner, without editing your files.",
  },
  cache: {
    label: "Reused result",
    explain: "The inputs of this run (sources, settings, test) were identical to an earlier run, so svkit reused that result instead of running the tool again.",
  },
  x_trace: {
    label: "X tracer",
    explain: "Follows an unknown value (X) in the waveform back through the logic to where it started, e.g. a register that is never reset or a memory word never written.",
  },
  tool: {
    label: "Tool",
    explain: "The simulator or synthesis tool that produced this run (e.g. Verilator, VCS, Yosys, Design Compiler).",
  },
} satisfies Record<string, Term>;

export type TermId = keyof typeof GLOSSARY;

export function explainText(id: TermId): string {
  const t: Term = GLOSSARY[id];
  return t.abbr ? `${t.explain} Also called ${t.abbr}.` : t.explain;
}

/** Glossary entry for a compare-table metric name, if it has one. */
export function metricTerm(metric: string): TermId | null {
  const map: Record<string, TermId> = {
    wns: "setup_slack", tns: "setup_tns", hold_wns: "hold_slack", hold_tns: "hold_tns",
    period: "clock_period", area_total: "area", "power.dynamic": "power_split", "power.leakage": "power_split",
  };
  if (metric.startsWith("vt_mix.")) return "vt_mix";
  return map[metric] ?? null;
}
