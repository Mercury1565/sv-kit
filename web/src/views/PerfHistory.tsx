import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ECharts } from "echarts/core";
import { api } from "../api/client";
import type { PerfHistoryPoint } from "../api/types";
import { axisStyle, baseOption, chartPng, EChart, type ChartTheme } from "../components/EChart";
import { ErrorState, Loading } from "../components/States";
import { downloadDataUrl, fileSlug } from "../lib/download";
import { fmtCompact, fmtDate, fmtInt, fmtNum, fmtPs, isNum } from "../lib/format";
import { useAsync, useServerEvents } from "../lib/hooks";
import { navigate } from "../lib/router";

/** Bars shown at once: one categorical hue each, in fixed order, never cycled (8 validated slots). */
export const HISTORY_WINDOW = 8;

/** What names a run on its bar: the batch's name, else the run's own, else the batch id, else the run id. */
export function pointLabel(p: PerfHistoryPoint): string {
  return p.group_name || p.run_name || p.group_id || p.run_id;
}

/** [start, end) of the window ending `back` windows before the newest point. */
export function historyWindow(n: number, back: number): [number, number] {
  const end = Math.max(Math.min(n, n - back * HISTORY_WINDOW), Math.min(n, HISTORY_WINDOW));
  return [Math.max(0, end - HISTORY_WINDOW), end];
}

type Metric = "cpi" | "tpi";
const META: Record<Metric, { title: string; axis: string; digits: number }> = {
  cpi: { title: "Cycles per instruction", axis: "cycles per instruction", digits: 3 },
  tpi: { title: "Time per instruction", axis: "time per instruction (ps)", digits: 1 },
};

function HistoryChart({ metric, points, test }: { metric: Metric; points: PerfHistoryPoint[]; test: string }) {
  const ref = useRef<ECharts | null>(null);
  const M = META[metric];
  const has = points.some((p) => isNum(p[metric]));
  const option = useCallback(
    (t: ChartTheme) => {
      const ax = axisStyle(t);
      return {
        ...baseOption(t),
        title: { text: `${M.title} · ${test}`, left: 8, top: 0, textStyle: { color: t.fg, fontSize: 12, fontWeight: 600 } },
        grid: { left: 64, right: 16, top: 56, bottom: 64 },
        tooltip: {
          ...(baseOption(t).tooltip as object),
          trigger: "item",
          formatter: (prm: { dataIndex: number }) => {
            const p = points[prm.dataIndex];
            const rows = [
              `<b>${esc(pointLabel(p))}</b>`,
              `${esc(p.run_id)}${p.group_id ? ` · batch ${esc(p.group_id)}` : ""} · ${esc(fmtDate(p.created_at))}`,
              `cycles per instruction <b>${fmtNum(p.cpi, 3)}</b>`,
              `time per instruction <b>${fmtNum(p.tpi, 1)} ps</b> (clock ${fmtPs(p.period)}${p.period_source ? ` from ${esc(p.period_source)}` : ""})`,
              `${fmtInt(p.cycles)} cycles · ${fmtInt(p.instrs)} instructions`,
            ];
            if (p.cached_from) rows.push(`reused the result of ${esc(p.cached_from)}`);
            return rows.join("<br/>");
          },
        },
        xAxis: {
          type: "category",
          data: points.map((p) => p.run_id),
          ...ax,
          axisLabel: {
            ...ax.axisLabel,
            interval: 0,
            width: 104,
            overflow: "truncate",
            fontSize: 10,
            formatter: (_v: string, i: number) => `${pointLabel(points[i])}\n${fmtDate(points[i].created_at)}`,
          },
        },
        yAxis: { type: "value", name: M.axis, ...ax, nameTextStyle: { ...ax.nameTextStyle, align: "left" }, axisLabel: { ...ax.axisLabel, formatter: (v: number) => fmtCompact(v) } },
        series: [
          {
            type: "bar",
            name: M.axis,
            barMaxWidth: 40,
            // each run its own hue, in the fixed categorical order (oldest shown = slot 1)
            data: points.map((p, i) => ({ value: isNum(p[metric]) ? p[metric] : null, itemStyle: { color: t.series[i % t.series.length], borderRadius: [4, 4, 0, 0] } })),
            label: { show: true, position: "top", color: t.fg, fontSize: 11, formatter: (prm: { value: number }) => fmtNum(prm.value, M.digits) },
          },
        ],
      };
    },
    [points, metric, M, test],
  );
  const save = () => {
    if (ref.current) downloadDataUrl(`perf-history-${fileSlug(test)}-${metric}.png`, chartPng(ref.current));
  };
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>{M.title}</h3>
        <span className="muted small">lower is better</span>
        <span className="spacer" />
        <button className="small" onClick={save} disabled={!has} title={`Save this chart as a PNG image`}>
          Save image (PNG)
        </button>
      </div>
      <div className="panel-body">
        {has ? (
          <EChart
            option={option}
            height={280}
            chartRef={ref}
            ariaLabel={`${M.axis} of ${test} over ${points.length} runs`}
            onClick={(p) => navigate(`/runs/${points[p.dataIndex].run_id}`)}
          />
        ) : (
          <div className="muted small" style={{ padding: 12 }}>
            {metric === "tpi"
              ? "No clock period was known for these runs: set clock_period in svkit.toml, run synthesis or a clock sweep, or enter one."
              : "These runs reported no instruction count."}
          </div>
        )}
      </div>
    </div>
  );
}

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

/** Change from the previous run, in percent; lower is better for both CPI and TPI. */
export function delta(cur?: number | null, prev?: number | null): { text: string; tone: string } {
  if (!isNum(cur) || !isNum(prev) || prev === 0) return { text: "", tone: "" };
  const d = ((cur - prev) / prev) * 100;
  if (Math.abs(d) < 0.05) return { text: "±0%", tone: "muted" };
  return { text: `${d > 0 ? "+" : "−"}${fmtNum(Math.abs(d), 1)}%`, tone: d > 0 ? "worse" : "better" };
}

function Delta({ cur, prev }: { cur?: number | null; prev?: number | null }) {
  const d = delta(cur, prev);
  return <td className={`num ${d.tone}`}>{d.text}</td>;
}

/** One test's CPI and TPI across every run of it, one colored bar per run. */
export function PerfHistory({ test, tests, onTest, onClose }: { test: string; tests: string[]; onTest: (t: string) => void; onClose: () => void }) {
  const hist = useAsync(() => api.perfHistory(test), [test]);
  useServerEvents((ev) => {
    if (ev.type === "run.status" && (ev.status === "passed" || ev.status === "failed" || ev.status === "done")) hist.reload();
  });
  const [back, setBack] = useState(0);
  useEffect(() => setBack(0), [test]);
  const all = hist.data ?? [];
  const [start, end] = historyWindow(all.length, back);
  const points = useMemo(() => all.slice(start, end), [all, start, end]);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => box.current?.scrollIntoView?.({ block: "nearest" }), [test]);

  return (
    <div className="panel perf-history" ref={box}>
      <div className="panel-head">
        <h3>One test across runs</h3>
        <select value={test} onChange={(e) => onTest(e.target.value)} aria-label="Test to follow across runs" className="mono" style={{ maxWidth: 320 }}>
          {!tests.includes(test) && <option value={test}>{test}</option>}
          {tests.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        {all.length > HISTORY_WINDOW && (
          <span className="row" style={{ gap: 4 }}>
            <button className="small" onClick={() => setBack((b) => b + 1)} disabled={start === 0} aria-label="Older runs">
              ‹ Older
            </button>
            <span className="muted small">
              runs {start + 1}–{end} of {all.length}
            </span>
            <button className="small" onClick={() => setBack((b) => Math.max(0, b - 1))} disabled={back === 0} aria-label="Newer runs">
              Newer ›
            </button>
          </span>
        )}
        <span className="spacer" />
        <button className="ghost small" onClick={onClose} aria-label="Close the test history">
          Close
        </button>
      </div>
      <div className="panel-body">
        {hist.error ? (
          <ErrorState error={hist.error} onRetry={hist.reload} />
        ) : !hist.data ? (
          <Loading what={`Loading the runs of ${test}`} />
        ) : all.length === 0 ? (
          <div className="muted small">No run of {test} reported cycle and instruction counts yet.</div>
        ) : (
          <>
            <div className="muted small" style={{ marginBottom: 8 }}>
              Oldest on the left. Each color is one run, the same in both charts. Time per instruction uses the clock period each run finished with; it does not change later. Click a bar to open its run.
            </div>
            <div className="grid2">
              <HistoryChart metric="cpi" points={points} test={test} />
              <HistoryChart metric="tpi" points={points} test={test} />
            </div>
            <table className="t perf-history-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Batch / name</th>
                  <th>Made</th>
                  <th className="num">Cycles</th>
                  <th className="num">Instructions</th>
                  <th className="num">Cycles / instr.</th>
                  <th className="num">change</th>
                  <th className="num">Clock period</th>
                  <th className="num">Time / instr. (ps)</th>
                  <th className="num">change</th>
                </tr>
              </thead>
              <tbody>
                {points.map((p, i) => {
                  const prev = i > 0 ? points[i - 1] : start > 0 ? all[start - 1] : undefined;
                  return (
                    <tr key={p.run_id}>
                      <td className="mono nowrap">
                        <span className="swatch" style={{ background: `var(--series-${i + 1})` }} aria-hidden="true" />
                        <a href={`#/runs/${p.run_id}`}>{p.run_id}</a>
                      </td>
                      <td className="ellipsis" style={{ maxWidth: 260 }} title={[p.group_name, p.group_id, p.run_name].filter(Boolean).join(" · ")}>
                        {p.group_id ? <a href={`#/perf?group=${p.group_id}`}>{p.group_name || p.group_id}</a> : <span className="faint">single run</span>}
                        {p.run_name && <span className="muted"> · {p.run_name}</span>}
                      </td>
                      <td className="nowrap">{fmtDate(p.created_at)}</td>
                      <td className="num">{fmtInt(p.cycles)}</td>
                      <td className="num">{fmtInt(p.instrs)}</td>
                      <td className="num">{fmtNum(p.cpi, 3)}</td>
                      <Delta cur={p.cpi} prev={prev?.cpi} />
                      <td className="num">{fmtPs(p.period)}</td>
                      <td className="num">{fmtNum(p.tpi, 1)}</td>
                      <Delta cur={p.tpi} prev={prev?.tpi} />
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}
