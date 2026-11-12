import { useCallback, useMemo, useRef, useState } from "react";
import type { ECharts } from "echarts/core";
import { api } from "../api/client";
import type { Group, Job, PerfResult, PeriodChoice, PeriodFrom, Suite } from "../api/types";
import { JobButton } from "../components/JobButton";
import { NameEditor, NAME_MAX } from "../components/NameEditor";
import { RemoveBatchConfirm } from "../components/RemoveBatch";
import { useApp } from "../lib/appState";
import { projectSuites } from "./Tests";
import { axisStyle, baseOption, chartPng, EChart, renderPng, type ChartTheme } from "../components/EChart";
import { downloadBytes, downloadDataUrl, downloadText, fileSlug, toCsv } from "../lib/download";
import { dataUrlBytes, zipBytes, type ZipEntry } from "../lib/zip";
import { Empty, ErrorState, Loading } from "../components/States";
import { VirtualTable, type Column } from "../components/VirtualTable";
import { DASH, fmtCompact, fmtDate, fmtInt, fmtNum, fmtPs, isNum } from "../lib/format";
import { href, navigate } from "../lib/router";
import { Term } from "../components/Info";
import { PerfHistory } from "./PerfHistory";
import { useAsync, useServerEvents } from "../lib/hooks";
import { periodNote, periodShort, periodSourceText, periodsDiffer } from "../lib/perfPeriod";

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

// ------------------------------------------------------------------ where new runs take their clock period from
export const PERIOD_FROM: Record<PeriodFrom, { label: string; title: string }> = {
  toml: { label: "svkit.toml", title: "The clock_period knob's default in svkit.toml (else the latest synthesis run)" },
  sweep: { label: "Clock sweep", title: "The latest clock sweep's fastest passing period (else the latest synthesis run)" },
  manual: { label: "Enter it…", title: "A clock period you type in (ps)" },
};

/**
 * The project setting for where sim runs take their clock period from as they finish. Runs that
 * already finished keep theirs, so changing this never moves a number already in the table.
 */
export function PeriodPicker() {
  const state = useAsync(() => api.perfPeriod(), []);
  useServerEvents((ev) => {
    if (ev.type === "perf.period") state.reload();
  });
  const [draft, setDraft] = useState<string | null>(null);   // the manual period being typed
  const [err, setErr] = useState<string | null>(null);
  const value = state.data;
  const save = async (p: PeriodChoice) => {
    setErr(null);
    try {
      const next = await api.setPerfPeriod(p);
      state.setData(() => next);
      setDraft(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  if (!value || !(value.from in PERIOD_FROM)) return null;
  const text = draft ?? (value.from === "manual" ? String(value.ps) : "");
  const n = Number(text);
  const valid = text.trim() !== "" && Number.isFinite(n) && n > 0;
  const commit = () => {
    if (draft !== null && valid && n !== value.ps) void save({ from: "manual", ps: n });
  };
  const gets = isNum(value.period) ? `new runs get ${fmtPs(value.period)}${value.source && value.from !== "manual" ? ` (${value.source})` : ""}` : "new runs get no clock period yet";
  return (
    <span className="row" style={{ gap: 4 }} title="Each run keeps the clock period it finished with; this sets the one for runs that finish from now on">
      <label className="muted small" htmlFor="perf-period-from">
        Clock period for new runs
      </label>
      <select
        id="perf-period-from"
        value={value.from}
        title={PERIOD_FROM[value.from].title}
        onChange={(e) => {
          const from = e.target.value as PeriodFrom;
          // "Enter it…" starts from the period a run would get now
          if (from === "manual") void save({ from, ps: isNum(value.period) && value.period > 0 ? value.period : 1000 });
          else void save({ from });
        }}
      >
        {(Object.keys(PERIOD_FROM) as PeriodFrom[]).map((k) => (
          <option key={k} value={k} title={PERIOD_FROM[k].title}>
            {PERIOD_FROM[k].label}
          </option>
        ))}
      </select>
      {value.from === "manual" && (
        <>
          <input
            type="number"
            min={0}
            step="any"
            value={text}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => e.key === "Enter" && commit()}
            aria-label="Clock period in ps"
            aria-invalid={!valid}
            title="Clock period in picoseconds; press Enter to apply"
            style={{ width: 90 }}
          />
          <span className="muted small">ps</span>
        </>
      )}
      {value.from !== "manual" && <span className="muted small">{gets}</span>}
      {err && <span className="small worse" role="alert">{err}</span>}
    </span>
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

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

/** The per-program chart of one metric (on screen, or off screen for Save all). */
export function perfChartOption(metric: PerfMetric, rows: PerfResult[], scope: string) {
  const M = PERF_METRICS[metric];
  return (t: ChartTheme) => {
    const val = M.value;
    // worst first: highest CPI / TPI, lowest IPC
    const data = rows.filter((r) => isNum(val(r))).sort((a, b) => (M.better === "lower" ? val(b)! - val(a)! : val(a)! - val(b)!));
    const ax = axisStyle(t);
    // the clock period behind the numbers, on the chart itself so every saved image carries it
    const differ = periodsDiffer(data);
    return {
      ...baseOption(t),
      grid: { left: 64, right: 84, top: 68, bottom: (data.length > 25 ? 104 : 84) + (differ ? 14 : 0) },
      tooltip: {
        ...(baseOption(t).tooltip as object),
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (ps: { dataIndex: number }[]) => {
          const r = data[ps[0]?.dataIndex ?? 0];
          if (!r) return "";
          const src = periodSourceText(r.period_source);
          return `<b>${esc(r.test)}</b><br/>${esc(M.axis)} <b>${fmtNum(val(r), M.digits)}</b><br/>clock period ${esc(periodShort(r))}${src ? ` (${esc(src)})` : ""}`;
        },
      },
      dataZoom: data.length > 25 ? [{ type: "slider", height: 12, bottom: 4, start: 0, end: (25 / data.length) * 100, showDetail: false }] : [],
      xAxis: {
        type: "category",
        data: data.map((r) => r.test),
        ...ax,
        axisLabel: {
          ...ax.axisLabel,
          rotate: 35,
          interval: 0,
          width: 96,
          overflow: "truncate",
          fontFamily: t.mono,
          fontSize: 10,
          lineHeight: 14,
          formatter: (v: string, i: number) => (differ && data[i] ? `${v}\n${periodShort(data[i])}` : v),
        },
      },
      title: {
        text: `${M.axis[0].toUpperCase()}${M.axis.slice(1)} · ${scope}`,
        subtext: periodNote(data),
        left: 8,
        top: 0,
        itemGap: 4,
        textStyle: { color: t.fg, fontSize: 12, fontWeight: 600 },
        subtextStyle: { color: metric === "tpi" ? t.fg : t.muted, fontSize: 11, fontWeight: metric === "tpi" ? 600 : 400 },
      },
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
  };
}

/** group: a `sim --all` batch id; without one, the latest result per test across every run. */
export default function PerfView({ group }: { group?: string }) {
  const { project } = useApp();
  const suites = useMemo(() => projectSuites(project), [project]);
  const suite = suites.find((s) => s.default) ?? suites[0];
  const [picking, setPicking] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);   // the batch id being confirmed
  const openBatch = useCallback((job: Job) => {
    if (job.group_id) navigate(href("/perf", { group: job.group_id }));
  }, []);
  const perf = useAsync(() => api.perf(group), [group]);
  const batches = useAsync(() => api.groups("sim-all"), []);
  useServerEvents((ev) => {
    // new runs bring their own (fixed) clock period
    if (ev.type === "group.updated" || (ev.type === "run.status" && (ev.status === "done" || ev.status === "passed"))) perf.reload();
    if (ev.type === "group.updated") batches.reload();
    if (ev.type === "group.deleted") {
      batches.reload();
      if (ev.group_id === group) navigate(href("/perf", {}));
      else perf.reload();
    }
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

  const chartScope = group ? (current?.name ? `${current.name} (${group})` : group) : "latest result per test";
  const option = useMemo(() => perfChartOption(shown, rows, chartScope), [shown, rows, chartScope]);
  const [savingAll, setSavingAll] = useState(false);
  const saveAll = async () => {
    setSavingAll(true);
    try {
      const metrics = (Object.keys(PERF_METRICS) as PerfMetric[]).filter((k) => rows.some((r) => isNum(PERF_METRICS[k].value(r))));
      const width = Math.max(1200, 40 * rows.length + 160);
      const entries: ZipEntry[] = [];
      for (const k of metrics) entries.push({ name: `${exportBase}-${k}.png`, data: dataUrlBytes(await renderPng(perfChartOption(k, rows, chartScope), width, 380)) });
      entries.push({ name: `${exportBase}.csv`, data: "\uFEFF" + perfCsv(rows, stallKeys) });
      downloadBytes(`${exportBase}-charts.zip`, zipBytes(entries));
    } finally {
      setSavingAll(false);
    }
  };

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
          {scope} · cycles per instruction = cycles ÷ instructions · time per instruction = cycles per instruction × the clock period each run finished with
          {sources.length === 1 && (
            <>
              {" "}· period from <PeriodSource src={sources[0]} />
            </>
          )}
        </span>
        <span className="spacer" />
        <PeriodPicker />
        {picker}
        {group && current && <NameEditor what="batch" name={current.name} onSave={async (n) => { await api.renameGroup(group, n); batches.reload(); }} />}
        {group && current && (
          <button onClick={() => setRemoving((r) => (r === group ? null : group))} aria-expanded={removing === group} title="Remove this batch with all its runs and their files">
            Remove batch
          </button>
        )}
        <button className={picking ? "" : "primary"} onClick={() => setPicking((p) => !p)} disabled={!suite?.flow || !suite.tests.length} aria-expanded={picking} title="Choose which tests to run, then run them as one batch">
          Run tests…
        </button>
      </div>
      {removing && removing === group && current && (
        <RemoveBatchConfirm
          group={current}
          runs={Array.isArray(current.params?.tests) ? (current.params.tests as unknown[]).length : undefined}
          onCancel={() => setRemoving(null)}
          onRemoved={() => {
            setRemoving(null);
            batches.reload();
            navigate(href("/perf", {}));
          }}
        />
      )}
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
                    title={k === "tpi" && !hasTpi ? "No clock period known yet: set clock_period in svkit.toml, run synthesis or a clock sweep, or enter one" : `${PERF_METRICS[k].axis} (${PERF_METRICS[k].better} is better)`}
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
              <button onClick={saveAll} disabled={savingAll} title="One .zip with the cycles-per-instruction, instructions-per-cycle and time-per-instruction charts, and the table as CSV">
                {savingAll ? "Saving…" : "Save all charts (ZIP)"}
              </button>
            </div>
            <div className="panel-body">
              <EChart option={option} height={320} chartRef={chartRef} ariaLabel={`${M.axis} for ${rows.length} programs`} />
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
