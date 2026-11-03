// Mirrors src/svkit/schemas.py and docs/ARCHITECTURE.md §6 (contract).
// Units: time/slack/period ps, power mW, area = area_unit, wave time = timescale ticks.

export type Severity = "info" | "warning" | "error";

export interface Msg {
  severity: Severity;
  code?: string | null;
  text: string;
  file?: string | null;
  line?: number | null;
}

export interface TimingPoint {
  inst: string;
  pin: string;
  cell?: string | null;
  incr: number;
  arrival: number;
  rise_fall?: "r" | "f" | null;
  net?: string | null;
}

export interface TimingPath {
  startpoint: string;
  endpoint: string;
  path_group?: string | null;
  path_type: "max" | "min";
  slack: number;
  required?: number | null;
  arrival?: number | null;
  points: TimingPoint[];
}

export interface Power {
  dynamic?: number | null;
  leakage?: number | null;
  total?: number | null;
}

export interface SynthResult {
  schema_version: number;
  tool: string;
  top: string;
  period?: number | null;
  corner?: string | null;
  wns?: number | null;
  tns?: number | null;
  hold_wns?: number | null;
  hold_tns?: number | null;
  area_total?: number | null;
  area_unit: string;
  area_by_hier: Record<string, number>;
  cell_count?: number | null;
  cells_by_type: Record<string, number>;
  vt_mix: Record<string, number>;
  power: Power;
  paths: TimingPath[];
  warnings: Msg[];
  netlist?: string | null;
  extra: Record<string, unknown>;
}

export interface Divergence {
  line: number;
  expected?: string | null;
  actual?: string | null;
  time?: number | null;
  cycle?: number | null;
}

export interface PerfResult {
  test: string;
  cycles?: number | null;
  instrs?: number | null;
  cpi?: number | null;
  stalls: Record<string, number>;
  period?: number | null;
  period_source?: string | null;
  tpi?: number | null;
}

export interface SimResult {
  schema_version: number;
  tool: string;
  test: string;
  passed?: boolean | null;
  reason?: string | null;
  sim_time?: number | null;
  waves?: string | null;
  divergence?: Divergence | null;
  perf?: PerfResult | null;
  warnings: Msg[];
}

export type XCause =
  | "no_reset"
  | "uninit_memory"
  | "index_out_of_range"
  | "multi_driver"
  | "external_input"
  | "latch"
  | "unknown";

export interface DriverRef {
  kind: "assign" | "always_comb" | "always_ff" | "always_latch" | "port" | "initial" | "input" | "unknown";
  file?: string | null;
  line?: number | null;
  text?: string | null;
}

export interface XTraceNode {
  signal: string;
  time: number;
  value: string;
  driver?: DriverRef | null;
  cause?: XCause | null;
  note?: string | null;
  children: XTraceNode[];
}

export type Verdict = "better" | "worse" | "same" | "na";

export interface MetricDelta {
  metric: string;
  unit?: string | null;
  better: "higher" | "lower" | "none";
  values: (number | null)[];
  deltas: (number | null)[];
  pct: (number | null)[];
  verdict: Verdict[];
}

export interface CompareResult {
  run_ids: string[];
  labels: string[];
  metrics: MetricDelta[];
}

export interface SweepPoint {
  period: number;
  run_id: string;
  wns?: number | null;
  hold_wns?: number | null;
  area_total?: number | null;
  power_total?: number | null;
  passed: boolean;
  note?: string | null;
}

export interface SweepResult {
  group_id: string;
  knob: string;
  tol: number;
  points: SweepPoint[];
  t_min?: number | null;
  t_min_run_id?: string | null;
  status: "running" | "done" | "failed";
}

// ------------------------------------------------------------------ Run DB
export type RunKind = "sim" | "synth" | "sweep-point";
export type RunStatus = "queued" | "running" | "passed" | "failed" | "done" | "error" | "cancelled";
export const TERMINAL_STATUSES: RunStatus[] = ["passed", "failed", "done", "error", "cancelled"];

export interface RunSummary {
  id: string;
  kind: RunKind | string;
  adapter: string;
  status: RunStatus | string;
  created_at: number;
  test?: string | null;
  started_at?: number | null;
  finished_at?: number | null;
  duration_s?: number | null;
  git_sha?: string | null;
  git_dirty?: boolean | null;
  source_hash?: string | null;
  knobs: Record<string, unknown>;
  experiment?: string | null;
  cache_key?: string | null;
  cached_from?: string | null;
  exit_code?: number | null;
  artifacts: Record<string, string>;
  group_id?: string | null;
  message?: string | null;
  tags: string[];
}

export interface Run extends RunSummary {
  /** SynthResult for synth / sweep-point runs, SimResult for sim runs. */
  metrics?: SynthResult | SimResult | Record<string, unknown> | null;
}

export interface Group {
  id: string;
  kind: "sim-all" | "matrix" | "sweep" | string;
  created_at: number;
  status: string;
  params: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  runs?: RunSummary[];
}

export interface Project {
  name: string;
  root: string;
  top?: string | null;
  version?: string;
  git_sha?: string | null;
  git_dirty?: boolean | null;
  flows: { sim: string[]; synth: string[]; sta: string[] };
  knobs: string[];
  experiments: string[];
  tests: string[];
  adapters: Record<string, boolean>;
}

export type JobKind = "sim" | "sim-all" | "synth" | "matrix" | "sweep";

export interface Job {
  job_id: string;
  kind: JobKind | string;
  status: "running" | "done" | "error" | string;
  run_ids: string[];
  group_id?: string | null;
  args?: Record<string, unknown>;
  error?: string | null;
}

// ------------------------------------------------------------------ waves
export interface WaveMeta {
  file: string;
  format: string;
  timescale: { factor: number; unit: string };
  t_start: number;
  t_end: number;
}

export interface WaveVar {
  name: string;
  path: string;
  /** null for real-valued signals */
  width: number | null;
  kind: string;
  type?: string | null;
}

export interface WaveScope {
  path: string;
  scopes: { name: string; path: string }[];
  vars: WaveVar[];
}

export interface WaveSearchHit {
  path: string;
  width: number | null;
  kind: string;
}

/** [time, value]: value is a binary string over 01xz (MSB first), a real as text, or "*" = dense region. */
export type WaveChange = [number, string];

export interface WaveSignalData {
  /** null for real-valued signals */
  width: number | null;
  init: string;
  changes: WaveChange[];
  truncated: boolean;
}

export interface WaveData {
  signals: Record<string, WaveSignalData>;
}

export interface WaveDecode {
  kind: "enum" | "struct" | "plain";
  enum?: Record<string, string> | null;
  fields?: { name: string; msb: number; lsb: number; enum?: Record<string, string> | null }[] | null;
}

// ------------------------------------------------------------------ design
export interface DesignPort {
  name: string;
  dir?: string | null;
  width?: number | null;
}

export interface DesignHierarchy {
  top: string;
  modules: Record<
    string,
    { ports: (DesignPort | string)[]; instances: { name: string; module: string }[]; file?: string | null; line?: number | null }
  >;
}

export interface BlockNode {
  id: string;
  label: string;
  module?: string | null;
  ports?: (DesignPort | string)[];
}

export interface BlockEdge {
  src: string;
  dst: string;
  width?: number | null;
  net?: string | null;
}

export interface BlockGraph {
  nodes: BlockNode[];
  edges: BlockEdge[];
}

export interface ConeNode {
  id: string;
  cell?: string | null;
  pins?: (string | { name: string; dir?: string | null })[];
  on_path?: boolean;
  delay?: number | null;
}

export interface ConeEdge {
  src: string;
  dst: string;
  net?: string | null;
  on_path?: boolean;
}

export interface ConeGraph {
  nodes: ConeNode[];
  edges: ConeEdge[];
  unresolved: string[];
}

export interface SourceSlice {
  file: string;
  start: number;
  lines: string[];
}

// ------------------------------------------------------------------ events (WS /ws/events)
export type ServerEvent =
  | { type: "run.created"; run: RunSummary }
  | { type: "run.status"; run_id: string; status: string; message?: string | null }
  | { type: "run.log"; run_id: string; line: string }
  | { type: "group.updated"; group_id: string; status: string; result?: Record<string, unknown> | null }
  | { type: "job.updated"; job: Job };

export function isSynthResult(m: unknown): m is SynthResult {
  return !!m && typeof m === "object" && "area_by_hier" in (m as object);
}

export function isSimResult(m: unknown): m is SimResult {
  return !!m && typeof m === "object" && "test" in (m as object) && !("area_by_hier" in (m as object));
}
