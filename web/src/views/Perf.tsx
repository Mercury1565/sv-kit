import { useCallback, useMemo } from "react";
import { api } from "../api/client";
import type { Group, PerfResult } from "../api/types";
import { axisStyle, baseOption, EChart, type ChartTheme } from "../components/EChart";
import { Empty, ErrorState, Loading } from "../components/States";
import { VirtualTable, type Column } from "../components/VirtualTable";
import { DASH, fmtCompact, fmtDate, fmtInt, fmtNum, fmtPs, isNum } from "../lib/format";
import { href, navigate } from "../lib/router";
import { Term } from "../components/Info";
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

function batchLabel(g: Group): string {
  const n = Array.isArray(g.params?.tests) ? (g.params.tests as unknown[]).length : null;
  const parts = [g.id, fmtDate(g.created_at)];
  if (n !== null) parts.push(`${n} ${n === 1 ? "test" : "tests"}`);
  if (g.status !== "done") parts.push(g.status);
  return parts.join(" · ");
}

/** group: a `sim --all` batch id; without one, the latest result per test across every run. */
export default function PerfView({ group }: { group?: string }) {
  const perf = useAsync(() => api.perf(group), [group]);
  const batches = useAsync(() => api.groups("sim-all"), []);
  useServerEvents((ev) => {
    // TPI follows the latest synth / sweep period
    if (ev.type === "group.updated" || (ev.type === "run.status" && (ev.status === "done" || ev.status === "passed"))) perf.reload();
    if (ev.type === "group.updated") batches.reload();
  });
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
  const stallKeys = useMemo(() => Array.from(new Set(rows.flatMap((r) => Object.keys(r.stalls ?? {})))).sort(), [rows]);
  const sources = Array.from(new Set(rows.map((r) => r.period_source).filter(Boolean)));

  const option = useCallback(
    (t: ChartTheme) => {
      const data = rows.filter((r) => isNum(r.cpi)).sort((a, b) => b.cpi! - a.cpi!);
      const ax = axisStyle(t);
      return {
        ...baseOption(t),
        grid: { left: 64, right: 56, top: 34, bottom: data.length > 25 ? 104 : 84 },
        tooltip: { ...(baseOption(t).tooltip as object), trigger: "axis", axisPointer: { type: "shadow" } },
        dataZoom: data.length > 25 ? [{ type: "slider", height: 12, bottom: 4, start: 0, end: (25 / data.length) * 100, showDetail: false }] : [],
        xAxis: {
          type: "category",
          data: data.map((r) => r.test),
          ...ax,
          axisLabel: { ...ax.axisLabel, rotate: 35, interval: 0, width: 96, overflow: "truncate", fontFamily: t.mono, fontSize: 10 },
        },
        yAxis: { type: "value", name: "cycles per instruction", ...ax, nameTextStyle: { ...ax.nameTextStyle, align: "left" }, axisLabel: { ...ax.axisLabel, formatter: (v: number) => fmtCompact(v) } },
        series: [
          {
            type: "bar",
            name: "cycles per instruction",
            data: data.map((r) => r.cpi),
            itemStyle: { color: t.accent },
            barMaxWidth: 28,
            markLine: data.length
              ? { silent: true, symbol: "none", lineStyle: { color: t.faint }, data: [{ type: "average", name: "mean" }], label: { color: t.muted, position: "insideEndTop", formatter: (p: any) => `mean ${fmtNum(p.value, 2)}` } }
              : undefined,
          },
        ],
      };
    },
    [rows],
  );

  const cols: Column<PerfResult>[] = [
    { key: "test", header: "Program", width: "minmax(200px, 3fr)", render: (r) => <span className="mono">{r.test}</span>, title: (r) => r.test, sortValue: (r) => r.test },
    { key: "cycles", header: "Cycles", width: "120px", align: "right", render: (r) => fmtInt(r.cycles), sortValue: (r) => r.cycles },
    { key: "instrs", header: "Instructions", width: "120px", align: "right", render: (r) => fmtInt(r.instrs), sortValue: (r) => r.instrs },
    { key: "cpi", header: <Term id="cpi">Cycles / instr.</Term>, width: "130px", align: "right", render: (r) => fmtNum(r.cpi, 3), sortValue: (r) => r.cpi },
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
      </div>
      {rows.length === 0 && group ? (
        <Empty title={`No performance data in batch ${group}`}>
          None of the runs in this batch reported cycle and instruction counts. Pick another batch above.
        </Empty>
      ) : rows.length === 0 ? (
        <Empty title="No performance data yet" cmd="svkit sim --all">
          Configure <code>[perf].source</code> in <code>svkit.toml</code> so svkit can read cycle and instruction counts from your test outputs.
        </Empty>
      ) : (
        <>
          <div className="panel">
            <div className="panel-body">
              <EChart option={option} height={300} ariaLabel={`Cycles per instruction for ${rows.length} programs`} />
            </div>
          </div>
          <VirtualTable rows={rows} columns={cols} rowKey={(r) => r.test} height={Math.min(520, 26 * (rows.length + 1) + 4)} initialSort={{ key: "test", dir: 1 }} ariaLabel="Cycles and time per instruction per program" />
        </>
      )}
    </div>
  );
}
