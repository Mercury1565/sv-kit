import { useEffect, useMemo, useRef, useState } from "react";
import type { ECharts } from "echarts/core";
import { api } from "../api/client";
import type { PerfHistoryPoint } from "../api/types";
import { axisStyle, baseOption, chartPng, EChart, renderPng, type ChartTheme } from "../components/EChart";
import { ErrorState, Loading } from "../components/States";
import { downloadBytes, downloadDataUrl, fileSlug, toCsv } from "../lib/download";
import { dataUrlBytes, zipBytes, type ZipEntry } from "../lib/zip";
import { fmtCompact, fmtDate, fmtInt, fmtNum, fmtPs, isNum } from "../lib/format";
import { useAsync, useServerEvents } from "../lib/hooks";
import { periodNote, periodShort, periodsDiffer } from "../lib/perfPeriod";
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

/** The option for one history chart (on screen, or rendered off screen for Save all). */
export function historyOption(metric: Metric, points: PerfHistoryPoint[], test: string, note = "") {
  const M = META[metric];
  const differ = periodsDiffer(points);
  return (t: ChartTheme) => {
    const ax = axisStyle(t);
    return {
      ...baseOption(t),
      // the clock period on the chart itself, so every saved image carries it
      title: {
        text: `${M.title} · ${test}${note}`,
        subtext: periodNote(points),
        left: 8,
        top: 0,
        itemGap: 4,
        textStyle: { color: t.fg, fontSize: 12, fontWeight: 600 },
        subtextStyle: { color: metric === "tpi" ? t.fg : t.muted, fontSize: 11, fontWeight: metric === "tpi" ? 600 : 400 },
      },
      grid: { left: 64, right: 16, top: 68, bottom: differ ? 78 : 64 },
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
          formatter: (_v: string, i: number) => `${pointLabel(points[i])}\n${fmtDate(points[i].created_at)}${differ ? `\n${periodShort(points[i])}` : ""}`,
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
  };
}

// ------------------------------------------------------------------ save every chart at once
const PNG_W = 720;
const PNG_H = 340;

/** Every chart Save all writes for one test: CPI and TPI for each page of (at most 8) runs, oldest page first. */
export function historyExportPlan(all: PerfHistoryPoint[]): { file: string; metric: Metric; points: PerfHistoryPoint[]; note: string }[] {
  const windows: [number, number][] = [];
  for (let back = 0; ; back++) {
    const w = historyWindow(all.length, back);
    if (w[1] <= w[0] || windows.some(([a, b]) => a === w[0] && b === w[1])) break;
    windows.unshift(w);
    if (w[0] === 0) break;
  }
  const pad = (n: number) => String(n).padStart(String(all.length).length, "0");
  const out = [];
  for (const [a, b] of windows) {
    const pts = all.slice(a, b);
    const paged = windows.length > 1;
    for (const metric of ["cpi", "tpi"] as Metric[]) {
      if (!pts.some((p) => isNum(p[metric]))) continue;
      out.push({
        file: paged ? `${metric}-runs-${pad(a + 1)}-${pad(b)}.png` : `${metric}.png`,
        metric,
        points: pts,
        note: paged ? ` · runs ${a + 1}–${b} of ${all.length}` : "",
      });
    }
  }
  return out;
}

/** One row per run of the test, oldest first: raw numbers, as on screen. */
export function historyCsv(test: string, all: PerfHistoryPoint[]): string {
  return toCsv(
    ["test", "run", "batch", "batch_name", "run_name", "made", "cycles", "instructions", "cycles_per_instruction", "clock_period_ps", "time_per_instruction_ps", "clock_period_from"],
    all.map((p) => [test, p.run_id, p.group_id, p.group_name, p.run_name, new Date(p.created_at * 1000).toISOString(), p.cycles, p.instrs, p.cpi, p.period, p.tpi, p.period_source]),
  );
}

async function historyEntries(test: string, all: PerfHistoryPoint[], folder: string): Promise<ZipEntry[]> {
  const out: ZipEntry[] = [];
  for (const c of historyExportPlan(all)) {
    out.push({ name: `${folder}${c.file}`, data: dataUrlBytes(await renderPng(historyOption(c.metric, c.points, test, c.note), PNG_W, PNG_H)) });
  }
  if (all.length) out.push({ name: `${folder}runs.csv`, data: historyCsv(test, all) });
  return out;
}

function HistoryChart({ metric, points, test }: { metric: Metric; points: PerfHistoryPoint[]; test: string }) {
  const ref = useRef<ECharts | null>(null);
  const M = META[metric];
  const has = points.some((p) => isNum(p[metric]));
  const option = useMemo(() => historyOption(metric, points, test), [points, metric, test]);
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
            height={300}
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
  const [saving, setSaving] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const saveAll = async (every: boolean) => {
    setSaveError(null);
    try {
      let entries: ZipEntry[] = [];
      if (!every) {
        setSaving("Saving…");
        entries = await historyEntries(test, all, "");
      } else {
        for (const [i, t] of tests.entries()) {
          setSaving(`Saving ${i + 1} of ${tests.length}…`);
          const h = t === test ? all : await api.perfHistory(t);
          if (h.length) entries.push(...(await historyEntries(t, h, `${fileSlug(t) || "test"}/`)));
        }
      }
      if (!entries.length) throw new Error("no run reported cycle and instruction counts");
      downloadBytes(every ? "perf-history-all-tests.zip" : `perf-history-${fileSlug(test)}.zip`, zipBytes(entries));
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(null);
    }
  };

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
        {saving ? (
          <span className="muted small" role="status">
            {saving}
          </span>
        ) : (
          <>
            <button
              className="small"
              onClick={() => saveAll(false)}
              disabled={!all.length}
              title={`One .zip with the cycles- and time-per-instruction charts of ${test} for all ${all.length} runs (every page), and the numbers as CSV`}
            >
              Save all charts (ZIP)
            </button>
            <button
              className="small"
              onClick={() => saveAll(true)}
              disabled={!tests.length}
              title={`The same for each of the ${tests.length} tests, one folder per test, in one .zip`}
            >
              Every test (ZIP)
            </button>
          </>
        )}
        <button className="ghost small" onClick={onClose} aria-label="Close the test history">
          Close
        </button>
      </div>
      <div className="panel-body">
        {saveError && <div className="small worse" style={{ marginBottom: 8 }}>Could not save the charts: {saveError}</div>}
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
