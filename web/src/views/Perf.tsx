import { useCallback, useMemo, useRef, useState } from "react";
import type { ECharts } from "echarts/core";
import { api } from "../api/client";
import type { Group, Job, PerfResult, Suite } from "../api/types";
import { JobButton } from "../components/JobButton";
import { NameEditor, NAME_MAX } from "../components/NameEditor";
import { useApp } from "../lib/appState";
import { projectSuites } from "./Tests";
import { axisStyle, baseOption, chartPng, EChart, type ChartTheme } from "../components/EChart";
import { downloadDataUrl, downloadText, fileSlug, toCsv } from "../lib/download";
import { Empty, ErrorState, Loading } from "../components/States";
import { VirtualTable, type Column } from "../components/VirtualTable";
import { DASH, fmtCompact, fmtDate, fmtInt, fmtNum, fmtPs, isNum } from "../lib/format";
import { href, navigate } from "../lib/router";
import { Term } from "../components/Info";
import { PerfHistory } from "./PerfHistory";
import { useAsync, useServerEvents } from "../lib/hooks";

function PeriodSource({ src }: { src?: string | null }) {
  if (!src) return <span className="faint">{DASH}</span>;
  const [kind, id] = src.split(":");
  const link = kind === "sweep" ? `#/sweep/${id}` : kind === "synth" ? `#/synth/${id}` : undefined;
  return link ? (
    <a href={link} className="mono" title={src}>
      {kind} {id}
    </a>
  ) : (
    <span className="mono">{src}</span>
  );
}

export function batchLabel(g: Group): string {
  const n = Array.isArray(g.params?.tests) ? (g.params.tests as unknown[]).length : null;
  const parts = [...(g.name ? [g.name] : []), g.id, fmtDate(g.created_at)];
  if (n !== null) parts.push(`${n} ${n === 1 ? "test" : "tests"}`);
  if (g.status !== "done") parts.push(g.status);
  return parts.join(" · ");
}

// ------------------------------------------------------------------ chart metric and exports
export type PerfMetric = "cpi" | "ipc" | "tpi";

/** Instructions per cycle: the reciprocal of CPI. */
export function ipcOf(r: PerfResult): number | null {
  return isNum(r.cycles) && isNum(r.instrs) && r.cycles > 0 ? r.instrs / r.cycles : isNum(r.cpi) && r.cpi > 0 ? 1 / r.cpi : null;
}

export const PERF_METRICS: Record<PerfMetric, { short: string; label: string; axis: string; digits: number; value: (r: PerfResult) => number | null | undefined; better: "lower" | "higher" }> = {
  cpi: { short: "CPI", label: "Cycles / instr.", axis: "cycles per instruction", digits: 3, value: (r) => r.cpi, better: "lower" },
  ipc: { short: "IPC", label: "Instr. / cycle", axis: "instructions per cycle", digits: 3, value: ipcOf, better: "higher" },
  tpi: { short: "TPI", label: "Time / instr.", axis: "time per instruction (ps)", digits: 1, value: (r) => r.tpi, better: "lower" },
};

/** The perf table as CSV: raw numbers (no thousands separators or units), one row per program. */
export function perfCsv(rows: PerfResult[], stallKeys: string[]): string {
  const header = ["program", "cycles", "instructions", "cycles_per_instruction", "instructions_per_cycle", "clock_period_ps", "time_per_instruction_ps", ...stallKeys.map((k) => `stall_cycles_${k}`), "clock_period_from"];
  return toCsv(
    header,
    rows.map((r) => [r.test, r.cycles, r.instrs, r.cpi, ipcOf(r), r.period, r.tpi, ...stallKeys.map((k) => r.stalls?.[k]), r.period_source]),
  );
}

const PICK_KEY = "svkit.perf.tests";

function loadPick(project: string, tests: string[]): Set<string> {
  try {
    const v = JSON.parse(localStorage.getItem(`${PICK_KEY}.${project}`) ?? "null");
    if (Array.isArray(v)) return new Set(v.filter((x) => tests.includes(x)));
  } catch {
    /* storage unavailable: start with every test */
  }
  return new Set(tests);
}

function savePick(project: string, pick: Set<string>) {
  try {
    localStorage.setItem(`${PICK_KEY}.${project}`, JSON.stringify(Array.from(pick)));
  } catch {
    /* not remembered; fine */
  }
}

/** Choose which tests to run as one batch, name it, and run it. */
export function TestPicker({ suite, project, onClose, onDone }: { suite: Suite; project: string; onClose: () => void; onDone: (job: Job) => void }) {
  const tests = suite.tests;
  const [pick, setPick] = useState<Set<string>>(() => loadPick(project, tests));
  const [filter, setFilter] = useState("");
  const [name, setName] = useState("");
  const f = filter.trim().toLowerCase();
  const shown = f ? tests.filter((t) => t.toLowerCase().includes(f)) : tests;
  const update = (next: Set<string>) => {
    setPick(next);
    savePick(project, next);
  };
  const toggle = (t: string) => {
    const n = new Set(pick);
    if (n.has(t)) n.delete(t);
    else n.add(t);
    update(n);
  };
  const chosen = tests.filter((t) => pick.has(t));
  const all = chosen.length === tests.length;
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Run tests</h3>
        <span className="muted small">
          {chosen.length} of {tests.length} chosen{suite.label && ` · ${suite.label.toLowerCase()}`} · runs as one batch you can pick above
        </span>
        <span className="spacer" />
        <button className="ghost" onClick={onClose} aria-label="Close test picker">
          Close
        </button>
      </div>
      <div className="panel-body test-picker">
        <div className="row wrap" style={{ gap: 6 }}>
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter tests" aria-label="Filter tests" style={{ width: 180 }} />
          <button onClick={() => update(new Set([...pick, ...shown]))} disabled={shown.every((t) => pick.has(t))}>
            {f ? `Select ${shown.length} shown` : "Select all"}
          </button>
          <button onClick={() => update(new Set([...pick].filter((t) => !shown.includes(t))))} disabled={!shown.some((t) => pick.has(t))}>
            {f ? `Clear ${shown.length} shown` : "Select none"}
          </button>
        </div>
        <div className="tp-grid" role="group" aria-label="Tests to run">
          {shown.map((t) => (
            <label key={t} title={t}>
              <input type="checkbox" checked={pick.has(t)} onChange={() => toggle(t)} />
              <span>{t}</span>
            </label>
          ))}
          {shown.length === 0 && <span className="muted small" style={{ padding: "2px 6px" }}>No test matches “{filter}”.</span>}
        </div>
        <div className="row wrap" style={{ gap: 6 }}>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name this batch (optional)"
            aria-label="Name for this batch"
            maxLength={NAME_MAX}
            style={{ width: 260 }}
          />
          <JobButton
            kind="sim-all"
            primary
            disabled={!chosen.length}
            args={{ flow: suite.flow, ...(all ? {} : { tests: chosen }), ...(name.trim() ? { name: name.trim() } : {}) }}
            onSubmitted={() => setName("")}
            onDone={onDone}
          >
            {all ? `Run all ${tests.length} tests` : `Run ${chosen.length} ${chosen.length === 1 ? "test" : "tests"}`}
          </JobButton>
        </div>
      </div>
    </div>
  );
}

/** group: a `sim --all` batch id; without one, the latest result per test across every run. */
export default function PerfView({ group }: { group?: string }) {
  const { project } = useApp();
  const suites = useMemo(() => projectSuites(project), [project]);
  const suite = suites.find((s) => s.default) ?? suites[0];
  const [picking, setPicking] = useState(false);
  const openBatch = useCallback((job: Job) => {
    if (job.group_id) navigate(href("/perf", { group: job.group_id }));
  }, []);
  const perf = useAsync(() => api.perf(group), [group]);
  const batches = useAsync(() => api.groups("sim-all"), []);
  useServerEvents((ev) => {
    // TPI follows the latest synth / sweep period
    if (ev.type === "group.updated" || (ev.type === "run.status" && (ev.status === "done" || ev.status === "passed"))) perf.reload();
    if (ev.type === "group.updated") batches.reload();
  });
  const current = (batches.data ?? []).find((g) => g.id === group);
  const batchList = useMemo(() => [...(batches.data ?? [])].sort((a, b) => b.created_at - a.created_at), [batches.data]);
  const picker = (
    <select
      value={group ?? ""}
      onChange={(e) => navigate(href("/perf", { group: e.target.value }))}
      aria-label="Which sim --all batch to show"
      title="Each svkit sim --all is one batch"
      style={{ maxWidth: 360 }}
    >
      <option value="">Latest result per test (all runs)</option>
      {group && !batchList.some((g) => g.id === group) && <option value={group}>{group}</option>}
      {batchList.map((g) => (
        <option key={g.id} value={g.id}>
          {batchLabel(g)}
        </option>
      ))}
    </select>
  );
  const rows = perf.data ?? [];
  const [metric, setMetric] = useState<PerfMetric>("cpi");
  const chartRef = useRef<ECharts | null>(null);
  const [historyOf, setHistoryOf] = useState<string | null>(null);
  const testNames = useMemo(() => rows.map((r) => r.test).sort(), [rows]);
  const hasTpi = rows.some((r) => isNum(r.tpi));
  const shown: PerfMetric = metric === "tpi" && !hasTpi ? "cpi" : metric;
  const M = PERF_METRICS[shown];
  const exportBase = `perf-${group ? `${group}${current?.name ? `-${fileSlug(current.name)}` : ""}` : "latest"}`;
  const saveImage = () => {
    if (chartRef.current) downloadDataUrl(`${exportBase}-${shown}.png`, chartPng(chartRef.current));
  };
  const stallKeys = useMemo(() => Array.from(new Set(rows.flatMap((r) => Object.keys(r.stalls ?? {})))).sort(), [rows]);
  const sources = Array.from(new Set(rows.map((r) => r.period_source).filter(Boolean)));

  const option = useCallback(
    (t: ChartTheme) => {
      const val = M.value;
      // worst first: highest CPI / TPI, lowest IPC
      const data = rows.filter((r) => isNum(val(r))).sort((a, b) => (M.better === "lower" ? val(b)! - val(a)! : val(a)! - val(b)!));
      const ax = axisStyle(t);
      return {
        ...baseOption(t),
        grid: { left: 64, right: 84, top: 56, bottom: data.length > 25 ? 104 : 84 },
        tooltip: { ...(baseOption(t).tooltip as object), trigger: "axis", axisPointer: { type: "shadow" } },
        dataZoom: data.length > 25 ? [{ type: "slider", height: 12, bottom: 4, start: 0, end: (25 / data.length) * 100, showDetail: false }] : [],
        xAxis: {
          type: "category",
          data: data.map((r) => r.test),
          ...ax,
          axisLabel: { ...ax.axisLabel, rotate: 35, interval: 0, width: 96, overflow: "truncate", fontFamily: t.mono, fontSize: 10 },
        },
        title: { text: `${M.axis[0].toUpperCase()}${M.axis.slice(1)} · ${group ? (current?.name ? `${current.name} (${group})` : group) : "latest result per test"}`, left: 8, top: 0, textStyle: { color: t.fg, fontSize: 12, fontWeight: 600 } },
        yAxis: { type: "value", name: M.axis, ...ax, nameTextStyle: { ...ax.nameTextStyle, align: "left" }, axisLabel: { ...ax.axisLabel, formatter: (v: number) => fmtCompact(v) } },
        series: [
          {
            type: "bar",
            name: M.axis,
            data: data.map((r) => val(r)),
            itemStyle: { color: t.accent },
            barMaxWidth: 28,
            markLine: data.length
              ? { silent: true, symbol: "none", lineStyle: { color: t.faint }, data: [{ type: "average", name: "mean" }], label: { color: t.muted, position: "end", formatter: (p: any) => `mean\n${fmtNum(p.value, M.digits)}` } }
              : undefined,
          },
        ],
      };
    },
    [rows, M, group, current],
  );

  const cols: Column<PerfResult>[] = [
    { key: "test", header: "Program", width: "minmax(200px, 3fr)", render: (r) => <span className="mono">{r.test}</span>, title: (r) => r.test, sortValue: (r) => r.test },
    { key: "cycles", header: "Cycles", width: "120px", align: "right", render: (r) => fmtInt(r.cycles), sortValue: (r) => r.cycles },
    { key: "instrs", header: "Instructions", width: "120px", align: "right", render: (r) => fmtInt(r.instrs), sortValue: (r) => r.instrs },
    { key: "cpi", header: <Term id="cpi">Cycles / instr.</Term>, width: "130px", align: "right", render: (r) => fmtNum(r.cpi, 3), sortValue: (r) => r.cpi },
    { key: "ipc", header: <span title="instructions per cycle = instructions ÷ cycles (higher is better)">Instr. / cycle</span>, width: "120px", align: "right", render: (r) => fmtNum(ipcOf(r), 3), sortValue: (r) => ipcOf(r) },
    { key: "period", header: <Term id="clock_period" />, width: "130px", align: "right", render: (r) => fmtPs(r.period), sortValue: (r) => r.period },
    { key: "tpi", header: <Term id="tpi">Time / instr. (ps)</Term>, width: "150px", align: "right", render: (r) => fmtNum(r.tpi, 1), sortValue: (r) => r.tpi },
    ...stallKeys.map((k) => ({ key: `stall.${k}`, header: `stall cycles: ${k}`, width: "110px", align: "right" as const, render: (r: PerfResult) => fmtInt(r.stalls?.[k]), sortValue: (r: PerfResult) => r.stalls?.[k] })),
    { key: "src", header: "Clock period from", width: "140px", render: (r) => <PeriodSource src={r.period_source} /> },
  ];

  if (perf.error) return <div className="view"><ErrorState error={perf.error} feature="Performance (cycles and time per instruction)" onRetry={perf.reload} /></div>;
  if (!perf.data) return <div className="view"><Loading what="Loading perf" /></div>;
  const scope = group ? (
    <>
      batch <span className="mono">{group}</span>
      {current && Array.isArray(current.params?.tests) && <> ({(current.params.tests as unknown[]).length} tests)</>}
    </>
  ) : (
    "latest result per test, across all runs"
  );
  return (
    <div className="view">
      <div className="view-head">
        <h1>Performance</h1>
        <span className="muted small">
          {scope} · cycles per instruction = cycles ÷ instructions · time per instruction = cycles per instruction × clock period
          {sources.length === 1 && (
            <>
              {" "}· period from <PeriodSource src={sources[0]} />
            </>
          )}
        </span>
        <span className="spacer" />
        {picker}
        {group && current && <NameEditor what="batch" name={current.name} onSave={async (n) => { await api.renameGroup(group, n); batches.reload(); }} />}
        <button className={picking ? "" : "primary"} onClick={() => setPicking((p) => !p)} disabled={!suite?.flow || !suite.tests.length} aria-expanded={picking} title="Choose which tests to run, then run them as one batch">
          Run tests…
        </button>
      </div>
      {picking && suite && <TestPicker suite={suite} project={project?.name ?? ""} onClose={() => setPicking(false)} onDone={openBatch} />}
      {rows.length === 0 && group ? (
        <Empty title={`No performance data in batch ${group}`}>
          None of the runs in this batch reported cycle and instruction counts. Pick another batch above.
        </Empty>
      ) : rows.length === 0 ? (
        <Empty title="No performance data yet" cmd={["svkit sim --all", "svkit sim <test> <test> ..."]}>
          Configure <code>[perf].source</code> in <code>svkit.toml</code> so svkit can read cycle and instruction counts from your test outputs.
        </Empty>
      ) : (
        <>
          <div className="panel">
            <div className="panel-head">
              <span className="seg" role="radiogroup" aria-label="What the chart shows">
                {(Object.keys(PERF_METRICS) as PerfMetric[]).map((k) => (
                  <button
                    key={k}
                    role="radio"
                    aria-checked={shown === k}
                    className={shown === k ? "on" : ""}
                    onClick={() => setMetric(k)}
                    disabled={k === "tpi" && !hasTpi}
                    title={k === "tpi" && !hasTpi ? "No clock period known yet: run synthesis or a clock sweep, or set [perf].period" : `${PERF_METRICS[k].axis} (${PERF_METRICS[k].better} is better)`}
                  >
                    {PERF_METRICS[k].label}
                  </button>
                ))}
              </span>
              <span className="muted small">{M.better} is better</span>
              <span className="spacer" />
              <button onClick={saveImage} title={`Save this chart (${M.axis}, every program) as a PNG image`}>
                Save image (PNG)
              </button>
            </div>
            <div className="panel-body">
              <EChart option={option} height={300} chartRef={chartRef} ariaLabel={`${M.axis} for ${rows.length} programs`} />
            </div>
          </div>
          {historyOf && <PerfHistory test={historyOf} tests={testNames} onTest={setHistoryOf} onClose={() => setHistoryOf(null)} />}
          <div className="row" style={{ gap: 6 }}>
            <span className="muted small">
              {rows.length} {rows.length === 1 ? "program" : "programs"} · click one to see it across runs
            </span>
            <span className="spacer" />
            {!historyOf && (
              <select value="" onChange={(e) => e.target.value && setHistoryOf(e.target.value)} aria-label="See one test across runs" style={{ maxWidth: 260 }}>
                <option value="">See one test across runs…</option>
                {testNames.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            )}
            <button onClick={() => downloadText(`${exportBase}.csv`, perfCsv(rows, stallKeys), "text/csv")} title="Download this table as a CSV file (raw numbers, opens in any spreadsheet)">
              Download CSV
            </button>
          </div>
          <VirtualTable rows={rows} columns={cols} rowKey={(r) => r.test} onRowClick={(r) => setHistoryOf(r.test)} selected={historyOf ? new Set([historyOf]) : undefined} height={Math.min(520, 26 * (rows.length + 1) + 4)} initialSort={{ key: "test", dir: 1 }} ariaLabel="Cycles and time per instruction per program" />
        </>
      )}
    </div>
  );
}
