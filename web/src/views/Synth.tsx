import type { ReactNode } from "react";
import { Fragment, useCallback, useMemo, useState } from "react";
import { api } from "../api/client";
import { isSynthResult, type Msg, type Run, type RunSummary, type SynthResult, type TimingPath } from "../api/types";
import { baseOption, EChart, type ChartTheme } from "../components/EChart";
import { JobButton } from "../components/JobButton";
import { NameEditor, NAME_MAX } from "../components/NameEditor";
import { RemoveRuns } from "../components/RemoveRuns";
import { SlackChip, StatusChip } from "../components/StatusChip";
import { Empty, ErrorState, Loading } from "../components/States";
import { VirtualTable, type Column } from "../components/VirtualTable";
import { useApp } from "../lib/appState";
import { buildHierRows, type HierRow } from "../lib/hier";
import { DASH, fmtCompact, fmtDate, fmtInt, fmtNum, fmtPs, fmtSigned, isNum, plural } from "../lib/format";
import { useAsync, useServerEvents } from "../lib/hooks";
import { href } from "../lib/router";
import { RunsTable, isSynthKind } from "./Runs";
import { Term } from "../components/Info";
import { TagEditor } from "./RunDetail";

export default function SynthView({ id }: { id?: string }) {
  return id ? <SynthDashboard id={id} /> : <SynthIndex />;
}

// ------------------------------------------------------------------ index: synth runs + launchers
const areaText = (v: number | null | undefined) => (isNum(v) && Math.abs(v) >= 1e7 ? fmtCompact(v) : fmtNum(v, 1));

/** Headline QoR per run, each sortable (missing values sort last). */
export const QOR_COLUMNS: Column<RunSummary>[] = [
  {
    key: "q.period",
    header: <Term id="clock_period" />,
    width: "120px",
    align: "right",
    render: (r) => fmtPs(r.qor?.period),
    title: (r) => (isNum(r.qor?.period) && r.qor!.period! > 0 ? `${fmtNum(1e6 / r.qor!.period!, 1)} MHz` : undefined),
    sortValue: (r) => r.qor?.period,
  },
  {
    key: "q.wns",
    header: <Term id="setup_slack">Worst slack</Term>,
    width: "104px",
    align: "right",
    render: (r) => (isNum(r.qor?.wns) ? <span className={r.qor!.wns! < 0 ? "worse" : "better"}>{fmtPs(r.qor!.wns, true)}</span> : fmtPs(r.qor?.wns)),
    title: (r) => (isNum(r.qor?.hold_wns) ? `worst hold slack ${fmtPs(r.qor!.hold_wns, true)}` : undefined),
    sortValue: (r) => r.qor?.wns,
  },
  {
    key: "q.area",
    header: <Term id="area" />,
    width: "96px",
    align: "right",
    render: (r) => areaText(r.qor?.area_total),
    title: (r) => (isNum(r.qor?.area_total) ? `${fmtNum(r.qor!.area_total, 1)} ${(r.qor!.area_unit ?? "").replace("um^2", "µm²")}` : undefined),
    sortValue: (r) => r.qor?.area_total,
  },
  {
    key: "q.power",
    header: <span title="total power, mW">Power (mW)</span>,
    width: "100px",
    align: "right",
    render: (r) => fmtNum(r.qor?.power_total, 3),
    sortValue: (r) => r.qor?.power_total,
  },
  { key: "q.cells", header: "Cells", width: "72px", align: "right", render: (r) => fmtInt(r.qor?.cell_count), sortValue: (r) => r.qor?.cell_count },
];

/** The list row of a full run: headline QoR instead of the whole result. */
function summaryOf(r: Run): RunSummary {
  const m = isSynthResult(r.metrics) ? r.metrics : null;
  const { metrics: _m, ...rest } = r;
  return m ? { ...rest, qor: { period: m.period, wns: m.wns, hold_wns: m.hold_wns, area_total: m.area_total, area_unit: m.area_unit, cell_count: m.cell_count, power_total: m.power.total } } : rest;
}

function SynthIndex() {
  const { project } = useApp();
  const runs = useAsync(async () => (await api.allRuns()).filter((r) => isSynthKind(r.kind)), []);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [exp, setExp] = useState("");
  const [name, setName] = useState("");
  const named = name.trim() ? { name: name.trim() } : {};
  const clearName = () => setName("");
  useServerEvents((ev) => {
    if (ev.type === "run.created" && isSynthKind(ev.run.kind)) runs.setData((rs) => (rs ? [ev.run, ...rs] : rs));
    if (ev.type === "run.updated") runs.setData((rs) => rs?.map((r) => (r.id === ev.run.id ? { ...r, ...ev.run } : r)));
    if (ev.type === "run.deleted") {
      runs.setData((rs) => rs?.filter((r) => r.id !== ev.run_id));
      setSelected((s) => (s.has(ev.run_id) ? new Set([...s].filter((x) => x !== ev.run_id)) : s));
    }
    if (ev.type === "run.status") {
      runs.setData((rs) => rs?.map((r) => (r.id === ev.run_id ? { ...r, status: ev.status } : r)));
      // a finished run now has results: fetch its headline numbers
      if (ev.status === "done" || ev.status === "error")
        api.run(ev.run_id).then(
          (full) => isSynthKind(full.kind) && runs.setData((rs) => rs?.map((r) => (r.id === full.id ? { ...r, ...summaryOf(full) } : r))),
          () => {},
        );
    }
  });
  const toggle = useCallback(
    (rid: string) =>
      setSelected((s) => {
        const n = new Set(s);
        if (n.has(rid)) n.delete(rid);
        else n.add(rid);
        return n;
      }),
    [],
  );
  const noSynth = !project?.flows.synth.length;
  const sel = Array.from(selected);
  return (
    <div className="view fill">
      <div className="view-head">
        <h1>Synthesis</h1>
        <span className="spacer" />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name (optional)"
          aria-label="Name for the next synthesis run"
          title="Your own name for the next run, so you can find it later (rename any run from its page)"
          maxLength={NAME_MAX}
          style={{ width: 180 }}
        />
        <JobButton kind="synth" args={named} primary disabled={noSynth} onSubmitted={clearName}>
          Run synthesis
        </JobButton>
        {project && project.experiments.length > 0 && (
          <span className="row" style={{ gap: 4 }}>
            <select value={exp} onChange={(e) => setExp(e.target.value)} aria-label="Experiment" style={{ maxWidth: 200 }}>
              <option value="">experiment…</option>
              {project.experiments.map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
            <JobButton kind="synth" args={{ exp, ...named }} disabled={!exp || noSynth} onSubmitted={clearName}>
              Run
            </JobButton>
            <JobButton kind="matrix" args={named} disabled={noSynth} onSubmitted={clearName}>
              Run matrix ({project.experiments.length})
            </JobButton>
          </span>
        )}
        <a className={`btn ${sel.length < 2 ? "disabled" : ""}`} href={sel.length >= 2 ? `#/compare?ids=${sel.join(",")}` : undefined} aria-disabled={sel.length < 2} style={sel.length < 2 ? { opacity: 0.5, pointerEvents: "none" } : undefined}>
          Compare{sel.length ? ` (${sel.length})` : ""}
        </a>
        <RemoveRuns
          runs={runs.data ?? []}
          selected={selected}
          onRemoved={(ids) => {
            const gone = new Set(ids);
            runs.setData((rs) => rs?.filter((r) => !gone.has(r.id)));
            setSelected((s) => new Set([...s].filter((x) => !gone.has(x))));
          }}
        />
      </div>
      {runs.error ? (
        <ErrorState error={runs.error} onRetry={runs.reload} />
      ) : !runs.data ? (
        <Loading what="Loading synthesis runs" />
      ) : runs.data.length === 0 ? (
        <Empty title="No synthesis runs yet" cmd={["svkit synth", "svkit synth --matrix", "svkit sweep clock"]} />
      ) : (
        <div style={{ flex: "1 1 auto", minHeight: 240, display: "flex" }}>
          <RunsTable runs={runs.data} selected={selected} onToggle={toggle} extraColumns={[...QOR_COLUMNS, REPORTS_COLUMN]} hide={["git", "dur"]} />
        </div>
      )}
    </div>
  );
}

/** A per-row link that downloads the run's reports as one text file. */
export const REPORTS_COLUMN: Column<RunSummary> = {
  key: "reports",
  header: <span title="Download a run's reports as one .txt file">Reports</span>,
  width: "76px",
  render: (r) =>
    r.status === "queued" || r.status === "running" ? null : (
      <a href={api.reportsUrl(r.id)} download onClick={(e) => e.stopPropagation()} title={`Download the reports of ${r.id} as one .txt file`} aria-label={`Download the reports of ${r.id}`}>
        .txt
      </a>
    ),
};

// ------------------------------------------------------------------ dashboard
function Card({ label, value, sub, tone }: { label: ReactNode; value: string; sub?: string; tone?: "good" | "bad" }) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className={`value ${tone ?? ""}`} title={value}>
        {value}
      </div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

const slackTone = (v: number | null | undefined) => (isNum(v) ? (v < 0 ? "bad" : "good") : undefined);

function QoRCards({ m }: { m: SynthResult }) {
  return (
    <div className="cards">
      <Card label={<Term id="setup_slack" />} value={fmtPs(m.wns, true)} tone={slackTone(m.wns)} sub={m.paths[0] ? `slowest path ends at ${m.paths[0].endpoint}` : undefined} />
      <Card label={<Term id="setup_tns" />} value={fmtPs(m.tns, true)} tone={isNum(m.tns) && m.tns < 0 ? "bad" : undefined} />
      <Card label={<Term id="hold_slack" />} value={fmtPs(m.hold_wns, true)} tone={slackTone(m.hold_wns)} sub={isNum(m.hold_tns) ? `total hold violation ${fmtPs(m.hold_tns, true)}` : undefined} />
      <Card label={<Term id="clock_period" />} value={fmtPs(m.period)} sub={isNum(m.period) && m.period > 0 ? `${fmtNum(1e6 / m.period, 1)} MHz` : undefined} />
      <Card
        label={<Term id="area">{`Area (${m.area_unit.replace("um^2", "µm²")})`}</Term>}
        value={isNum(m.area_total) && Math.abs(m.area_total) >= 1e7 ? fmtCompact(m.area_total) : fmtNum(m.area_total, 1)}
        sub={isNum(m.area_total) && Math.abs(m.area_total) >= 1e7 ? fmtNum(m.area_total, 0) : undefined}
      />
      <Card label="Cells" value={fmtInt(m.cell_count)} />
      <Card label="Total power" value={isNum(m.power.total) ? `${fmtNum(m.power.total, 3)} mW` : fmtNum(m.power.total)} />
      <Card label={<Term id="power_split" />} value={`${fmtNum(m.power.dynamic, 3)} / ${fmtNum(m.power.leakage, 3)}`} sub="mW" />
      <Card label={<Term id="corner" />} value={m.corner ?? DASH} sub={m.tool} />
    </div>
  );
}

function VtMix({ mix }: { mix: Record<string, number> }) {
  const entries = Object.entries(mix).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((s, [, n]) => s + (isNum(n) ? n : 0), 0);
  const option = useCallback(
    (t: ChartTheme) => {
      const palette = [t.accent, t.warn, t.good, t.bad, t.faint, t.muted];
      return {
        ...baseOption(t),
        grid: { left: 8, right: 8, top: 8, bottom: 8 },
        tooltip: { ...(baseOption(t).tooltip as object), trigger: "item", formatter: (p: any) => `${p.seriesName}: ${fmtInt(p.value)} cells (${fmtNum(total ? (p.value / total) * 100 : 0, 1)}%)` },
        xAxis: { type: "value", max: total || 1, show: false },
        yAxis: { type: "category", data: ["cells"], show: false },
        series: entries.map(([cls, n], i) => ({
          name: cls,
          type: "bar",
          stack: "vt",
          barWidth: 22,
          data: [n],
          itemStyle: { color: palette[i % palette.length], borderColor: t.bg, borderWidth: 1 },
        })),
      };
    },
    [entries, total],
  );
  if (!entries.length) return <div className="muted small">No transistor speed classes (set <code>[library].vt_suffix</code> to map cell names to them).</div>;
  return (
    <div>
      <EChart option={option} height={40} ariaLabel={`Transistor speed mix: ${entries.map(([c, n]) => `${c} ${n}`).join(", ")}`} />
      <table className="t">
        <tbody>
          {entries.map(([c, n]) => (
            <tr key={c}>
              <td className="ellipsis" title={c} style={{ maxWidth: 220 }}>
                {c}
              </td>
              <td className="num">{fmtInt(n)}</td>
              <td className="num muted">{total ? fmtNum((n / total) * 100, 1) + "%" : DASH}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AreaTree({ m }: { m: SynthResult }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    // expand top + first level by default
    return new Set(["", ...Object.keys(m.area_by_hier).filter((k) => k && !/[/.]/.test(k.replace(/\[[^\]]*\]/g, "")))]);
  });
  const [sort, setSort] = useState<{ by: "area" | "name"; dir: 1 | -1 }>({ by: "area", dir: 1 });
  const rows = useMemo(() => buildHierRows(m.area_by_hier, { expanded, sort: sort.by, dir: sort.dir, total: m.area_total }), [m, expanded, sort]);
  const toggle = (p: string) =>
    setExpanded((s) => {
      const n = new Set(s);
      if (n.has(p)) n.delete(p);
      else n.add(p);
      return n;
    });
  const sortHdr = (by: "area" | "name", label: string) => (
    <span onClick={() => setSort((s) => (s.by === by ? { by, dir: (s.dir * -1) as 1 | -1 } : { by, dir: 1 }))} style={{ cursor: "pointer" }}>
      {label}
      {sort.by === by ? (sort.dir === 1 ? (by === "area" ? " ↓" : " ↑") : by === "area" ? " ↑" : " ↓") : ""}
    </span>
  );
  const cols: Column<HierRow>[] = [
    {
      key: "name",
      header: sortHdr("name", "Instance"),
      width: "minmax(200px, 1fr)",
      render: (r) => (
        <span className="row" style={{ paddingLeft: r.depth * 14, gap: 4 }}>
          <span
            style={{ width: 12, flexShrink: 0, cursor: r.hasChildren ? "pointer" : undefined, color: "var(--fg-faint)" }}
            onClick={() => r.hasChildren && toggle(r.path)}
            aria-label={r.hasChildren ? (expanded.has(r.path) ? "collapse" : "expand") : undefined}
          >
            {r.hasChildren ? (expanded.has(r.path) ? "▾" : "▸") : ""}
          </span>
          <span className="mono ellipsis">{r.name}</span>
        </span>
      ),
      title: (r) => r.path || "(top)",
    },
    { key: "area", header: sortHdr("area", "Area"), width: "110px", align: "right", render: (r) => fmtNum(r.area, 1) },
    {
      key: "pct",
      header: "%",
      width: "120px",
      render: (r) => (
        <span className="row" style={{ gap: 6 }}>
          <span style={{ flex: "1 1 auto", height: 6, background: "var(--neutral-bg)", borderRadius: 3, overflow: "hidden" }}>
            <span style={{ display: "block", height: "100%", width: `${Math.max(0, Math.min(100, r.pct ?? 0))}%`, background: "var(--accent)" }} />
          </span>
          <span className="num small" style={{ width: 44 }}>
            {r.pct === null ? DASH : fmtNum(r.pct, 1)}
          </span>
        </span>
      ),
    },
  ];
  if (!Object.keys(m.area_by_hier).length) return <div className="muted small">No per-hierarchy area in this report.</div>;
  return <VirtualTable rows={rows} columns={cols} rowKey={(r) => r.path || "(top)"} height={Math.min(420, 26 * (rows.length + 1) + 4)} onRowClick={(r) => r.hasChildren && toggle(r.path)} ariaLabel="Area by hierarchy" />;
}

function PathsTable({ runId, paths }: { runId: string; paths: TimingPath[] }) {
  const [open, setOpen] = useState<number | null>(0);
  if (!paths.length) return <div className="muted small">No timing paths in this run.</div>;
  return (
    <div className="scroll" style={{ maxHeight: 520 }}>
      <table className="t">
        <thead>
          <tr>
            <th style={{ width: 24 }} />
            <th>#</th>
            <th>Startpoint</th>
            <th>Endpoint</th>
            <th>Check</th>
            <th className="num"><Term id="slack">Slack (ps)</Term></th>
            <th className="num">Arrival (ps)</th>
            <th className="num">Points</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {paths.map((p, i) => (
            <Fragment key={i}>
              <tr onClick={() => setOpen(open === i ? null : i)} style={{ cursor: "pointer" }}>
                <td className="faint">{open === i ? "▾" : "▸"}</td>
                <td className="num">{i}</td>
                <td className="mono anywhere" style={{ maxWidth: 360 }}>{p.startpoint}</td>
                <td className="mono anywhere" style={{ maxWidth: 360 }}>{p.endpoint}</td>
                <td>{p.path_type === "min" ? "hold (too fast)" : "setup (too slow)"}</td>
                <td className={`num ${p.slack < 0 ? "worse" : "better"}`}>{fmtSigned(p.slack, 1)}</td>
                <td className="num">{fmtNum(p.arrival, 1)}</td>
                <td className="num">{fmtInt(p.points.length)}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <a href={href("/design", { cone: runId, path: i })} style={{ whiteSpace: "nowrap" }}>
                    Show gates
                  </a>
                </td>
              </tr>
              {open === i && (
                <tr>
                  <td />
                  <td colSpan={8} style={{ padding: 0 }}>
                    <table className="t" style={{ background: "var(--bg-sunken)" }}>
                      <thead>
                        <tr>
                          <th style={{ position: "static" }}>Instance</th>
                          <th style={{ position: "static" }}>Pin</th>
                          <th style={{ position: "static" }}>Cell</th>
                          <th className="num" style={{ position: "static" }}>Incr (ps)</th>
                          <th className="num" style={{ position: "static" }}>Arrival (ps)</th>
                          <th style={{ position: "static" }}>r/f</th>
                        </tr>
                      </thead>
                      <tbody>
                        {p.points.map((pt, k) => (
                          <tr key={k}>
                            <td className="mono anywhere">{pt.inst || "(port)"}</td>
                            <td className="mono">{pt.pin}</td>
                            <td className="mono">{pt.cell ?? DASH}</td>
                            <td className="num">{fmtNum(pt.incr, 1)}</td>
                            <td className="num">{fmtNum(pt.arrival, 1)}</td>
                            <td>{pt.rise_fall ?? ""}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function groupWarnings(ws: Msg[]): { key: string; code: string | null; severity: string; text: string; count: number; examples: Msg[] }[] {
  const m = new Map<string, { key: string; code: string | null; severity: string; text: string; count: number; examples: Msg[] }>();
  for (const w of ws) {
    const key = `${w.severity}|${w.code ?? w.text}`;
    const g = m.get(key);
    if (g) {
      g.count++;
      if (g.examples.length < 50) g.examples.push(w);
    } else m.set(key, { key, code: w.code ?? null, severity: w.severity, text: w.text, count: 1, examples: [w] });
  }
  const rank: Record<string, number> = { error: 0, warning: 1, info: 2 };
  return Array.from(m.values()).sort((a, b) => (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3) || b.count - a.count);
}

function Warnings({ ws }: { ws: Msg[] }) {
  const groups = useMemo(() => groupWarnings(ws), [ws]);
  const [open, setOpen] = useState<string | null>(null);
  if (!ws.length) return <div className="muted small">No warnings.</div>;
  return (
    <div className="scroll" style={{ maxHeight: 360 }}>
      <table className="t">
        <tbody>
          {groups.map((g) => (
            <Fragment key={g.key}>
              <tr onClick={() => setOpen(open === g.key ? null : g.key)} style={{ cursor: "pointer" }}>
                <td style={{ width: 70 }}>
                  <span className={`chip ${g.severity === "error" ? "bad" : g.severity === "warning" ? "warn" : ""}`}>{g.severity}</span>
                </td>
                <td className="mono" style={{ width: 90, whiteSpace: "nowrap" }}>{g.code ?? ""}</td>
                <td className="anywhere">{g.text}</td>
                <td className="num" style={{ width: 50 }}>
                  ×{fmtInt(g.count)}
                </td>
              </tr>
              {open === g.key &&
                g.examples.map((w, i) => (
                  <tr key={i}>
                    <td />
                    <td />
                    <td className="small muted anywhere" colSpan={2}>
                      {w.text}
                      {w.file ? <span className="mono"> — {w.file}{w.line ? `:${w.line}` : ""}</span> : null}
                    </td>
                  </tr>
                ))}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SynthDashboard({ id }: { id: string }) {
  const run = useAsync(() => api.run(id), [id]);
  useServerEvents((ev) => {
    if (ev.type === "run.status" && ev.run_id === id) run.reload();
    if (ev.type === "run.updated" && ev.run.id === id) run.setData((cur) => (cur ? { ...cur, name: ev.run.name } : cur));
  });
  if (run.error) return <div className="view"><ErrorState error={run.error} onRetry={run.reload} /></div>;
  if (!run.data) return <div className="view"><Loading what={`Loading ${id}`} /></div>;
  const r = run.data;
  const m = isSynthResult(r.metrics) ? r.metrics : null;
  return (
    <div className="view">
      <div className="view-head">
        <h1>
          Synthesis <span className="mono">{r.id}</span>
        </h1>
        <NameEditor name={r.name} onSave={async (n) => {
          const nr = await api.renameRun(r.id, n);
          run.setData(() => ({ ...r, ...nr }));
        }} />
        <StatusChip status={r.status} />
        {m && <SlackChip wns={m.wns} hold={m.hold_wns} />}
        <span className="muted small anywhere">
          {[r.adapter, r.experiment, fmtDate(r.created_at)].filter(Boolean).join(" · ")}
        </span>
        <span className="spacer" />
        {r.status !== "queued" && r.status !== "running" && (
          <a className="btn" href={api.reportsUrl(r.id)} download title="Download svkit's summary and every text report of this run as one .txt file">
            Download reports (.txt)
          </a>
        )}
        <a className="btn" href={`#/runs/${r.id}`}>
          Run details & log
        </a>
        {r.group_id && r.kind === "sweep-point" && (
          <a className="btn" href={`#/sweep/${r.group_id}`}>
            Sweep {r.group_id}
          </a>
        )}
      </div>
      <TagEditor run={r} onChange={(nr) => run.setData(() => nr)} />
      {!m ? (
        <Empty title={r.status === "running" || r.status === "queued" ? "Synthesis is still running" : "No synthesis results for this run"}>
          {r.message && <div className="small anywhere">{r.message}</div>}
        </Empty>
      ) : (
        <>
          <QoRCards m={m} />
          <div className="grid2">
            <div className="panel">
              <div className="panel-head">
                <h3>Area by hierarchy</h3>
                <span className="muted small">{plural(Object.keys(m.area_by_hier).length, "entry", "entries")}</span>
              </div>
              <div className="panel-body flush">
                <AreaTree m={m} />
              </div>
            </div>
            <div className="panel">
              <div className="panel-head">
                <h3><Term id="vt_mix" /></h3>
              </div>
              <div className="panel-body">
                <VtMix mix={m.vt_mix} />
              </div>
            </div>
          </div>
          <div className="panel">
            <div className="panel-head">
              <h3><Term id="timing_path" /></h3>
              <span className="muted small">{plural(m.paths.length, "path")}, worst first</span>
            </div>
            <PathsTable runId={r.id} paths={m.paths} />
          </div>
          <div className="panel">
            <div className="panel-head">
              <h3>Warnings</h3>
              <span className="muted small">{plural(m.warnings.length, "message")}</span>
            </div>
            <div className="panel-body flush">
              <Warnings ws={m.warnings} />
            </div>
          </div>
          {Object.keys(m.extra).length > 0 && (
            <details className="panel">
              <summary className="panel-head" style={{ cursor: "pointer" }}>
                Tool extras ({m.tool})
              </summary>
              <pre className="log">{JSON.stringify(m.extra, null, 2)}</pre>
            </details>
          )}
        </>
      )}
    </div>
  );
}
