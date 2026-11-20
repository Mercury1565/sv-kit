// Batches compared: the arithmetic and geometric mean of CPI and TPI over each batch's tests, one
// colored bar per batch (the same color in all four charts), like "One test across runs".
import { useEffect, useMemo, useRef, useState } from "react";
import type { ECharts } from "echarts/core";
import { api } from "../api/client";
import type { PerfBatch } from "../api/types";
import { axisStyle, baseOption, chartPng, EChart, renderPng, type ChartTheme } from "../components/EChart";
import { ErrorState, Loading } from "../components/States";
import { downloadBytes, downloadDataUrl, toCsv } from "../lib/download";
import { dataUrlBytes, zipBytes, type ZipEntry } from "../lib/zip";
import { fmtCompact, fmtDate, fmtNum, isNum } from "../lib/format";
import { useAsync, useServerEvents } from "../lib/hooks";
import { periodNote } from "../lib/perfPeriod";
import { batchMeans, batchName, commonTests, periodRange, testSetsDiffer, type BatchMeans, type MeanKind, type MeanMetric } from "../lib/perfMeans";
import { href, navigate } from "../lib/router";
import { delta, HISTORY_WINDOW, historyWindow } from "./PerfHistory";

type Scope = "own" | "common";

const METRIC: Record<MeanMetric, { title: string; axis: string; digits: number }> = {
  cpi: { title: "Cycles per instruction", axis: "cycles per instruction", digits: 3 },
  tpi: { title: "Time per instruction", axis: "time per instruction (ps)", digits: 1 },
};
const KIND: Record<MeanKind, { title: string; short: string }> = {
  arith: { title: "arithmetic mean", short: "mean" },
  geo: { title: "geometric mean", short: "geomean" },
};
const CHARTS: [MeanMetric, MeanKind][] = [
  ["cpi", "arith"],
  ["cpi", "geo"],
  ["tpi", "arith"],
  ["tpi", "geo"],
];

/** The tests the bars average, as a phrase for chart subtitles and the CSV. */
export function scopeText(scope: Scope, common: string[]): string {
  return scope === "common" ? `over the ${common.length} ${common.length === 1 ? "test" : "tests"} every batch ran` : "over each batch's own tests";
}

/** The option for one chart: one bar per batch, its own hue (in fixed order), oldest on the left. */
export function batchMeansOption(metric: MeanMetric, kind: MeanKind, bars: BatchMeans[], scopeLine: string, note = "") {
  const M = METRIC[metric];
  const ranges = bars.map((b) => periodRange(b.period));
  const differ = new Set(ranges).size > 1;
  return (t: ChartTheme) => {
    const ax = axisStyle(t);
    const tpi = metric === "tpi";
    return {
      ...baseOption(t),
      // the clock period and the tests averaged, on the chart itself, so a saved image carries them
      title: {
        text: `${M.title} · ${KIND[kind].title}${note}`,
        subtext: `${periodNote(bars.flatMap((b) => b.results))}\nAveraged ${scopeLine}`,
        left: 8,
        top: 0,
        itemGap: 4,
        textStyle: { color: t.fg, fontSize: 12, fontWeight: 600 },
        subtextStyle: { color: tpi ? t.fg : t.muted, fontSize: 11, fontWeight: tpi ? 600 : 400, lineHeight: 15 },
      },
      grid: { left: 64, right: 16, top: 84, bottom: differ ? 72 : 58 },
      tooltip: {
        ...(baseOption(t).tooltip as object),
        trigger: "item",
        formatter: (prm: { dataIndex: number }) => {
          const b = bars[prm.dataIndex];
          const m = b[metric];
          return [
            `<b>${esc(batchName(b.batch))}</b>`,
            `batch ${esc(b.batch.group_id)} · ${esc(fmtDate(b.batch.created_at))}`,
            `${esc(M.axis)}, ${KIND[kind].title}: <b>${fmtNum(m[kind], M.digits)}</b>`,
            `${KIND.arith.short} ${fmtNum(m.arith, M.digits)} · ${KIND.geo.short} ${fmtNum(m.geo, M.digits)} over ${m.n} ${m.n === 1 ? "test" : "tests"}`,
            `clock period ${esc(periodRange(b.period))}`,
          ].join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: bars.map((b) => b.batch.group_id),
        ...ax,
        axisLabel: {
          ...ax.axisLabel,
          interval: 0,
          width: 104,
          overflow: "truncate",
          fontSize: 10,
          formatter: (_v: string, i: number) => {
            const b = bars[i];
            return `${batchName(b.batch)}\n${fmtDate(b.batch.created_at)}\n${b[metric].n} ${b[metric].n === 1 ? "test" : "tests"}${differ ? `\n${ranges[i]}` : ""}`;
          },
        },
      },
      yAxis: { type: "value", name: M.axis, ...ax, nameTextStyle: { ...ax.nameTextStyle, align: "left" }, axisLabel: { ...ax.axisLabel, formatter: (v: number) => fmtCompact(v) } },
      series: [
        {
          type: "bar",
          name: `${M.axis}, ${KIND[kind].title}`,
          barMaxWidth: 40,
          data: bars.map((b, i) => ({ value: b[metric][kind], itemStyle: { color: t.series[i % t.series.length], borderRadius: [4, 4, 0, 0] } })),
          label: { show: true, position: "top", color: t.fg, fontSize: 11, formatter: (prm: { value: number }) => fmtNum(prm.value, M.digits) },
        },
      ],
    };
  };
}

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

/** One row per batch, oldest first: the four means, how many tests each averages, the periods. */
export function batchMeansCsv(rows: BatchMeans[], scopeLine: string): string {
  return toCsv(
    ["batch", "batch_name", "made", "tests_averaged", "cpi_arithmetic_mean", "cpi_geometric_mean", "tests_with_tpi", "tpi_arithmetic_mean_ps", "tpi_geometric_mean_ps", "clock_period_min_ps", "clock_period_max_ps", "averaged"],
    rows.map((b) => [
      b.batch.group_id, b.batch.group_name, new Date(b.batch.created_at * 1000).toISOString(), b.cpi.n, b.cpi.arith, b.cpi.geo,
      b.tpi.n, b.tpi.arith, b.tpi.geo, b.period?.[0], b.period?.[1], scopeLine,
    ]),
  );
}

/** Every chart Save all writes: the four charts for each page of (at most 8) batches, oldest page first. */
export function batchExportPlan(all: BatchMeans[]): { file: string; metric: MeanMetric; kind: MeanKind; bars: BatchMeans[]; note: string }[] {
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
    const bars = all.slice(a, b);
    const paged = windows.length > 1;
    for (const [metric, kind] of CHARTS) {
      if (!bars.some((x) => isNum(x[metric][kind]))) continue;
      out.push({
        file: `${metric}-${KIND[kind].short}${paged ? `-batches-${pad(a + 1)}-${pad(b)}` : ""}.png`,
        metric,
        kind,
        bars,
        note: paged ? ` · batches ${a + 1}–${b} of ${all.length}` : "",
      });
    }
  }
  return out;
}

const PNG_W = 720;
const PNG_H = 360;

function MeanChart({ metric, kind, bars, scopeLine }: { metric: MeanMetric; kind: MeanKind; bars: BatchMeans[]; scopeLine: string }) {
  const ref = useRef<ECharts | null>(null);
  const M = METRIC[metric];
  const has = bars.some((b) => isNum(b[metric][kind]));
  const option = useMemo(() => batchMeansOption(metric, kind, bars, scopeLine), [metric, kind, bars, scopeLine]);
  const save = () => {
    if (ref.current) downloadDataUrl(`perf-batches-${metric}-${KIND[kind].short}.png`, chartPng(ref.current));
  };
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>
          {M.title}, {KIND[kind].title}
        </h3>
        <span className="muted small">lower is better</span>
        <span className="spacer" />
        <button className="small" onClick={save} disabled={!has} title="Save this chart as a PNG image">
          Save image (PNG)
        </button>
      </div>
      <div className="panel-body">
        {has ? (
          <EChart
            option={option}
            height={320}
            chartRef={ref}
            ariaLabel={`${M.axis}, ${KIND[kind].title}, of ${bars.length} batches`}
            onClick={(p) => navigate(href("/perf", { group: bars[p.dataIndex].batch.group_id }))}
          />
        ) : (
          <div className="muted small" style={{ padding: 12 }}>
            {metric === "tpi"
              ? "No clock period was known for these batches: set clock_period in svkit.toml, run synthesis or a clock sweep, or enter one."
              : "These batches reported no instruction counts."}
          </div>
        )}
      </div>
    </div>
  );
}

function Delta({ cur, prev }: { cur?: number | null; prev?: number | null }) {
  const d = delta(cur, prev);
  return <td className={`num ${d.tone}`}>{d.text}</td>;
}

/** Each test batch's mean CPI and TPI (arithmetic and geometric), side by side. */
export function PerfBatches({ onClose }: { onClose: () => void }) {
  const data = useAsync(() => api.perfBatches(), []);
  useServerEvents((ev) => {
    if (ev.type === "group.updated" || ev.type === "group.deleted") data.reload();
  });
  const batches: PerfBatch[] = data.data ?? [];
  const common = useMemo(() => commonTests(batches), [batches]);
  const differ = useMemo(() => testSetsDiffer(batches), [batches]);
  const [scope, setScope] = useState<Scope>("own");
  const eff: Scope = differ && scope === "common" && common.length ? "common" : "own";
  const scopeLine = scopeText(eff, common);
  const all = useMemo(() => {
    const only = eff === "common" ? new Set(common) : undefined;
    return batches.map((b) => batchMeans(b, only));
  }, [batches, eff, common]);
  const [back, setBack] = useState(0);
  useEffect(() => setBack(0), [batches.length]);
  const [start, end] = historyWindow(all.length, back);
  const bars = useMemo(() => all.slice(start, end), [all, start, end]);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => box.current?.scrollIntoView?.({ block: "nearest" }), []);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const saveAll = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const entries: ZipEntry[] = [];
      for (const c of batchExportPlan(all))
        entries.push({ name: c.file, data: dataUrlBytes(await renderPng(batchMeansOption(c.metric, c.kind, c.bars, scopeLine, c.note), PNG_W, PNG_H)) });
      entries.push({ name: "batches.csv", data: "﻿" + batchMeansCsv(all, scopeLine) });
      downloadBytes(`perf-batches${eff === "common" ? "-common-tests" : ""}.zip`, zipBytes(entries));
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="panel perf-history" ref={box}>
      <div className="panel-head">
        <h3>Batches compared</h3>
        {differ && (
          <span className="seg" role="radiogroup" aria-label="Which tests each mean covers">
            <button role="radio" aria-checked={eff === "own"} className={eff === "own" ? "on" : ""} onClick={() => setScope("own")} title="Each batch's mean covers every test that batch ran">
              Each batch's tests
            </button>
            <button
              role="radio"
              aria-checked={eff === "common"}
              className={eff === "common" ? "on" : ""}
              onClick={() => setScope("common")}
              disabled={!common.length}
              title={common.length ? `Only the ${common.length} tests every batch ran, so every bar averages the same programs` : "No test was run in every batch"}
            >
              Only tests in every batch{common.length ? ` (${common.length})` : ""}
            </button>
          </span>
        )}
        {all.length > HISTORY_WINDOW && (
          <span className="row" style={{ gap: 4 }}>
            <button className="small" onClick={() => setBack((b) => b + 1)} disabled={start === 0} aria-label="Older batches">
              ‹ Older
            </button>
            <span className="muted small">
              batches {start + 1}–{end} of {all.length}
            </span>
            <button className="small" onClick={() => setBack((b) => Math.max(0, b - 1))} disabled={back === 0} aria-label="Newer batches">
              Newer ›
            </button>
          </span>
        )}
        <span className="spacer" />
        {saving ? (
          <span className="muted small" role="status">
            Saving…
          </span>
        ) : (
          <button className="small" onClick={saveAll} disabled={!all.length} title={`One .zip with the four charts for all ${all.length} batches (every page), and the means as CSV`}>
            Save all charts (ZIP)
          </button>
        )}
        <button className="ghost small" onClick={onClose} aria-label="Close the batch comparison">
          Close
        </button>
      </div>
      <div className="panel-body">
        {saveError && <div className="small worse" style={{ marginBottom: 8 }}>Could not save the charts: {saveError}</div>}
        {data.error ? (
          <ErrorState error={data.error} onRetry={data.reload} />
        ) : !data.data ? (
          <Loading what="Loading the batches" />
        ) : all.length === 0 ? (
          <div className="muted small">No batch reported cycle and instruction counts yet. Run tests with Run tests… to make one.</div>
        ) : (
          <>
            <div className="muted small" style={{ marginBottom: 8 }}>
              Oldest on the left. Each color is one batch, the same in every chart. The arithmetic mean is the plain average; the geometric mean (the n-th root of the product)
              keeps one long-running program from dominating and is the usual way to summarize a benchmark suite.
              {differ && eff === "own" && <> The batches ran different tests, so their means cover different programs; pick <i>Only tests in every batch</i> to compare like with like.</>}{" "}
              Click a bar to open its batch.
            </div>
            <div className="mean-grid">
              {CHARTS.map(([m, k]) => (
                <MeanChart key={`${m}-${k}`} metric={m} kind={k} bars={bars} scopeLine={scopeLine} />
              ))}
            </div>
            <table className="t perf-history-table">
              <thead>
                <tr>
                  <th>Batch</th>
                  <th>Made</th>
                  <th className="num">Tests</th>
                  <th className="num">CPI mean</th>
                  <th className="num">change</th>
                  <th className="num">CPI geomean</th>
                  <th className="num">change</th>
                  <th className="num">Clock period</th>
                  <th className="num">TPI mean (ps)</th>
                  <th className="num">change</th>
                  <th className="num">TPI geomean (ps)</th>
                  <th className="num">change</th>
                </tr>
              </thead>
              <tbody>
                {bars.map((b, i) => {
                  const prev = i > 0 ? bars[i - 1] : start > 0 ? all[start - 1] : undefined;
                  return (
                    <tr key={b.batch.group_id}>
                      <td className="ellipsis" style={{ maxWidth: 260 }} title={[b.batch.group_name, b.batch.group_id].filter(Boolean).join(" · ")}>
                        <span className="swatch" style={{ background: `var(--series-${i + 1})` }} aria-hidden="true" />
                        <a href={`#/perf?group=${b.batch.group_id}`}>{batchName(b.batch)}</a>
                        {b.batch.group_name && <span className="muted mono"> · {b.batch.group_id}</span>}
                      </td>
                      <td className="nowrap">{fmtDate(b.batch.created_at)}</td>
                      <td className="num">{b.cpi.n}</td>
                      <td className="num">{fmtNum(b.cpi.arith, 3)}</td>
                      <Delta cur={b.cpi.arith} prev={prev?.cpi.arith} />
                      <td className="num">{fmtNum(b.cpi.geo, 3)}</td>
                      <Delta cur={b.cpi.geo} prev={prev?.cpi.geo} />
                      <td className="num nowrap">{periodRange(b.period)}</td>
                      <td className="num">{fmtNum(b.tpi.arith, 1)}</td>
                      <Delta cur={b.tpi.arith} prev={prev?.tpi.arith} />
                      <td className="num">{fmtNum(b.tpi.geo, 1)}</td>
                      <Delta cur={b.tpi.geo} prev={prev?.tpi.geo} />
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
