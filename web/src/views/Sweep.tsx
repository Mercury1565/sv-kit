import { useCallback, useState } from "react";
import { api } from "../api/client";
import type { SweepResult } from "../api/types";
import { axisStyle, baseOption, EChart, type ChartTheme } from "../components/EChart";
import { JobButton } from "../components/JobButton";
import { Empty, ErrorState, Loading } from "../components/States";
import { useApp } from "../lib/appState";
import { DASH, fmtAgo, fmtCompact, fmtNum, fmtPs, fmtSigned, isNum } from "../lib/format";
import { useAsync, useServerEvents } from "../lib/hooks";
import { navigate } from "../lib/router";

export default function SweepView({ gid }: { gid?: string }) {
  return gid ? <SweepDetail gid={gid} /> : <SweepIndex />;
}

function SweepLauncher() {
  const { project } = useApp();
  const [tol, setTol] = useState(50);
  const [jobs, setJobs] = useState(2);
  return (
    <span className="row" style={{ gap: 6 }}>
      <label className="field">
        tol (ps)
        <input type="number" min={1} value={tol} onChange={(e) => setTol(Number(e.target.value))} style={{ width: 70 }} />
      </label>
      <label className="field">
        jobs
        <input type="number" min={1} max={8} value={jobs} onChange={(e) => setJobs(Number(e.target.value))} style={{ width: 54 }} />
      </label>
      <JobButton kind="sweep" args={{ tol, jobs }} primary disabled={!project?.flows.synth.length || !(tol > 0)}>
        Sweep clock
      </JobButton>
    </span>
  );
}

function SweepIndex() {
  const groups = useAsync(() => api.groups("sweep"), []);
  useServerEvents((ev) => {
    if (ev.type === "group.updated") groups.reload();
  });
  return (
    <div className="view">
      <div className="view-head">
        <h1>Clock sweep</h1>
        <span className="spacer" />
        <SweepLauncher />
      </div>
      {groups.error ? (
        <ErrorState error={groups.error} onRetry={groups.reload} />
      ) : !groups.data ? (
        <Loading />
      ) : groups.data.length === 0 ? (
        <Empty title="No sweeps yet" cmd="svkit sweep clock --tol 50">
          A sweep finds the tightest clock period that meets setup and hold.
        </Empty>
      ) : (
        <div className="panel scroll">
          <table className="t">
            <thead>
              <tr>
                <th>Sweep</th>
                <th>Status</th>
                <th className="num">Tolerance</th>
                <th className="num">Started</th>
              </tr>
            </thead>
            <tbody>
              {groups.data
                .slice()
                .sort((a, b) => b.created_at - a.created_at)
                .map((g) => (
                  <tr key={g.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/sweep/${g.id}`)}>
                    <td className="mono">
                      <a href={`#/sweep/${g.id}`}>{g.id}</a>
                    </td>
                    <td>{g.status}</td>
                    <td className="num">{isNum(g.params.tol) ? `${g.params.tol} ps` : DASH}</td>
                    <td className="num">{fmtAgo(g.created_at)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function sweepChartOption(sw: SweepResult) {
  return (t: ChartTheme) => {
    const pts = sw.points.filter((p) => isNum(p.period));
    const mk = (pass: boolean) =>
      pts.filter((p) => p.passed === pass).map((p) => ({ value: [p.period, isNum(p.wns) ? p.wns : null], run_id: p.run_id }));
    const ax = axisStyle(t);
    return {
      ...baseOption(t),
      grid: [
        { left: 72, right: 72, top: 30, height: "52%" },
        { left: 72, right: 72, top: "72%", bottom: 32 },
      ],
      legend: { top: 0, right: 8, textStyle: { color: t.muted, fontSize: 11 }, data: ["pass", "fail", "area", "power"] },
      tooltip: {
        ...(baseOption(t).tooltip as object),
        trigger: "item",
        formatter: (p: any) => {
          const pt = pts.find((x) => x.run_id === p.data?.run_id) ?? pts[p.dataIndex];
          if (!pt) return "";
          return [
            `<b>${pt.run_id}</b> · period ${fmtPs(pt.period)}`,
            `WNS ${fmtSigned(pt.wns, 1)} ps · hold ${fmtSigned(pt.hold_wns, 1)} ps`,
            `area ${fmtNum(pt.area_total, 1)} · power ${fmtNum(pt.power_total, 3)} mW`,
            pt.passed ? "✓ meets timing" : "✕ violates",
            "<i>click to open the synthesis run</i>",
          ].join("<br>");
        },
      },
      xAxis: [
        { type: "value", gridIndex: 0, name: "period (ps)", nameLocation: "middle", nameGap: 24, scale: true, ...ax, axisLabel: { ...ax.axisLabel, formatter: (v: number) => fmtCompact(v) } },
        { type: "value", gridIndex: 1, scale: true, ...ax, axisLabel: { ...ax.axisLabel, formatter: (v: number) => fmtCompact(v) } },
      ],
      yAxis: [
        { type: "value", gridIndex: 0, name: "WNS (ps)", scale: true, ...ax, axisLabel: { ...ax.axisLabel, formatter: (v: number) => fmtCompact(v) } },
        { type: "value", gridIndex: 1, name: "area", scale: true, ...ax, splitLine: { show: false }, axisLabel: { ...ax.axisLabel, formatter: (v: number) => fmtCompact(v) } },
        { type: "value", gridIndex: 1, name: "mW", scale: true, position: "right", ...ax, splitLine: { show: false }, axisLabel: { ...ax.axisLabel, formatter: (v: number) => fmtCompact(v) } },
      ],
      series: [
        {
          name: "slack",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: pts.map((p) => [p.period, isNum(p.wns) ? p.wns : null]),
          symbol: "none",
          lineStyle: { color: t.faint, width: 1 },
          silent: true,
          markLine: {
            silent: true,
            symbol: "none",
            label: { color: t.muted, fontSize: 11 },
            data: [
              { yAxis: 0, lineStyle: { color: t.faint, type: "solid" }, label: { formatter: "WNS = 0", position: "insideStartTop" } },
              ...(isNum(sw.t_min)
                ? [{ xAxis: sw.t_min, lineStyle: { color: t.good, type: "dashed", width: 1.5 }, label: { formatter: `T_min ${fmtPs(sw.t_min)}`, position: "insideEndTop", color: t.good } }]
                : []),
            ],
          },
        },
        { name: "pass", type: "scatter", xAxisIndex: 0, yAxisIndex: 0, data: mk(true), symbol: "circle", symbolSize: 9, itemStyle: { color: t.good }, cursor: "pointer" },
        { name: "fail", type: "scatter", xAxisIndex: 0, yAxisIndex: 0, data: mk(false), symbol: "diamond", symbolSize: 10, itemStyle: { color: t.bad }, cursor: "pointer" },
        { name: "area", type: "line", xAxisIndex: 1, yAxisIndex: 1, data: pts.map((p) => ({ value: [p.period, isNum(p.area_total) ? p.area_total : null], run_id: p.run_id })), symbol: "circle", symbolSize: 5, itemStyle: { color: t.accent }, lineStyle: { color: t.accent, width: 1.5 } },
        { name: "power", type: "line", xAxisIndex: 1, yAxisIndex: 2, data: pts.map((p) => ({ value: [p.period, isNum(p.power_total) ? p.power_total : null], run_id: p.run_id })), symbol: "rect", symbolSize: 5, itemStyle: { color: t.warn }, lineStyle: { color: t.warn, width: 1.5, type: "dashed" } },
      ],
    };
  };
}

function SweepDetail({ gid }: { gid: string }) {
  const sw = useAsync(() => api.sweep(gid), [gid]);
  useServerEvents((ev) => {
    if (ev.type === "group.updated" && ev.group_id === gid) {
      if (ev.result && "points" in ev.result) sw.setData(() => ev.result as unknown as SweepResult);
      else sw.reload();
    }
    if (ev.type === "run.status" && sw.data?.status === "running" && sw.data.points.some((p) => p.run_id === ev.run_id)) sw.reload();
  });
  const option = useCallback((t: ChartTheme) => (sw.data ? sweepChartOption(sw.data)(t) : {}), [sw.data]);
  if (sw.error) return <div className="view"><ErrorState error={sw.error} feature="Sweep results" onRetry={sw.reload} /></div>;
  if (!sw.data) return <div className="view"><Loading what="Loading sweep" /></div>;
  const s = sw.data;
  const tminPt = s.points.find((p) => p.run_id === s.t_min_run_id);
  return (
    <div className="view">
      <div className="view-head">
        <h1>
          Sweep <span className="mono">{s.group_id}</span>
        </h1>
        <span className={`chip ${s.status === "done" ? "good" : s.status === "failed" ? "bad" : "run"}`}>{s.status}</span>
        <span className="muted small">
          knob {s.knob} · tol {fmtNum(s.tol, 0)} ps · {s.points.length} points
        </span>
        <span className="spacer" />
        <SweepLauncher />
      </div>
      <div className="cards">
        <div className="card">
          <div className="label">T_min</div>
          <div className={`value ${isNum(s.t_min) ? "good" : ""}`}>{isNum(s.t_min) ? fmtPs(s.t_min) : s.status === "running" ? "searching…" : "none passed"}</div>
          {isNum(s.t_min) && <div className="sub">{fmtNum(1e6 / s.t_min, 1)} MHz</div>}
        </div>
        <div className="card">
          <div className="label">T_min run</div>
          <div className="value mono">{s.t_min_run_id ? <a href={`#/synth/${s.t_min_run_id}`}>{s.t_min_run_id}</a> : DASH}</div>
          {tminPt && <div className="sub">WNS {fmtSigned(tminPt.wns, 1)} ps · area {fmtNum(tminPt.area_total, 0)}</div>}
        </div>
      </div>
      {s.points.length === 0 ? (
        <Empty title="No points yet">The first synthesis runs of this sweep are still going.</Empty>
      ) : (
        <div className="panel">
          <div className="panel-body">
            <EChart
              option={option}
              height={440}
              ariaLabel={`WNS vs clock period for ${s.points.length} points; T_min ${isNum(s.t_min) ? s.t_min + " ps" : "not found"}`}
              onClick={(p) => {
                const rid = (p.data as { run_id?: string } | undefined)?.run_id;
                if (rid) navigate(`/synth/${rid}`);
              }}
            />
          </div>
        </div>
      )}
      {s.points.length > 0 && (
        <div className="panel scroll">
          <table className="t">
            <thead>
              <tr>
                <th className="num">Period</th>
                <th>Run</th>
                <th className="num">WNS</th>
                <th className="num">Hold WNS</th>
                <th className="num">Area</th>
                <th className="num">Power (mW)</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {s.points.map((p) => (
                <tr key={p.run_id} style={{ cursor: "pointer", fontWeight: p.run_id === s.t_min_run_id ? 600 : undefined }} onClick={() => navigate(`/synth/${p.run_id}`)}>
                  <td className="num">{fmtPs(p.period)}</td>
                  <td className="mono">
                    <a href={`#/synth/${p.run_id}`}>{p.run_id}</a>
                    {p.run_id === s.t_min_run_id && <span className="chip good" style={{ marginLeft: 6 }}>T_min</span>}
                  </td>
                  <td className={`num ${isNum(p.wns) && p.wns < 0 ? "worse" : ""}`}>{fmtSigned(p.wns, 1)}</td>
                  <td className={`num ${isNum(p.hold_wns) && p.hold_wns < 0 ? "worse" : ""}`}>{fmtSigned(p.hold_wns, 1)}</td>
                  <td className="num">{fmtNum(p.area_total, 1)}</td>
                  <td className="num">{fmtNum(p.power_total, 3)}</td>
                  <td>
                    {p.passed ? <span className="chip good">✓ pass</span> : <span className="chip bad">✕ fail</span>}
                    {p.note && <span className="small muted anywhere" style={{ marginLeft: 6 }}>{p.note}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
