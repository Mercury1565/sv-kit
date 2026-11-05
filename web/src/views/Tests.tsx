import { useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Project, RunSummary, Suite } from "../api/types";
import { JobButton } from "../components/JobButton";
import { StatusChip } from "../components/StatusChip";
import { Empty, ErrorState, Loading } from "../components/States";
import { VirtualTable, type Column } from "../components/VirtualTable";
import { useApp } from "../lib/appState";
import { fmtAgo, fmtDate, fmtDuration, fmtInt } from "../lib/format";
import { useAsync, useServerEvents } from "../lib/hooks";
import { Info } from "../components/Info";
import { href, navigate } from "../lib/router";

/** Why a failed run has no first mismatch to jump to, in words the user can act on. */
export function noDivergenceReason(run?: Pick<RunSummary, "message" | "artifacts" | "cached_from">): string {
  const why = run?.message ? ` (${run.message})` : "";
  if (run && !Object.keys(run.artifacts ?? {}).some((n) => /^waves\./.test(n)))
    return `No mismatch to show: the test failed before producing output to compare${why}. Open "details" for the log, then run it again.`;
  return `No mismatch to show: the output matched or was not compared; the failure came from elsewhere${why}.`;
}

/** Button: ask the backend for the first divergence and open the waveform there. */
export function DivergenceLink({ runId, run, label = "Open waves at first mismatch", unit = false }: { runId: string; run?: RunSummary; label?: string; unit?: boolean }) {
  const [state, setState] = useState<"idle" | "busy" | string>("idle");
  const hasWaves = run ? Object.keys(run.artifacts ?? {}).some((n) => /^waves\./.test(n)) : true;
  const go = async () => {
    setState("busy");
    try {
      const d = await api.divergence(runId);
      if (d.time === null || d.time === undefined) {
        setState(
          d.source === "log"
            ? `the first error (log line ${d.line}) prints no time; opening the waveform at the start`
            : `the first mismatch (output line ${d.line}) could not be matched to a time in the waveform`,
        );
        navigate(href(`/waves/${runId}`, { auto: "top" }));
        return;
      }
      navigate(href(`/waves/${runId}`, { t: d.time, auto: "top" }));
    } catch (e) {
      if (e instanceof ApiError && e.notAvailable) {
        setState(noDivergenceReason(run));
        if (hasWaves) navigate(href(`/waves/${runId}`, { auto: "top" }));
      } else setState(String(e));
    }
  };
  return (
    <span className="row" style={{ gap: 6, minWidth: 0 }}>
      <button onClick={go} disabled={state === "busy"} style={{ flexShrink: 0, whiteSpace: "nowrap" }}>
        {label}
      </button>
      <Info term={unit ? "first_error" : "divergence"} />
      {state !== "idle" && state !== "busy" && <span className="small muted ellipsis" title={state}>{state}</span>}
    </span>
  );
}

export interface TestRow {
  test: string;
  run?: RunSummary;
  history: RunSummary[]; // newest first
}

/** Suites the dashboard shows; projects served by an older backend get one implicit suite. */
export function projectSuites(project?: Project | null): Suite[] {
  if (project?.suites?.length) return project.suites;
  const flow = project?.flows.sim[0] ?? "";
  return [{ id: "system", label: "Tests", flow, flows: project?.flows.sim ?? [], tests: project?.tests ?? [], unit: false, golden: true, default: true }];
}

/** The suite a run belongs to (runs recorded before flows were stored belong to the default suite). */
export function suiteOf(run: Pick<RunSummary, "flow">, suites: Suite[]): string | undefined {
  const s = run.flow ? suites.find((x) => x.flows.includes(run.flow!)) : undefined;
  return (s ?? suites.find((x) => x.default) ?? suites[0])?.id;
}

export function latestPerTest(runs: RunSummary[], tests: string[]): TestRow[] {
  const by = new Map<string, RunSummary[]>();
  for (const r of runs) {
    if (r.kind !== "sim" || !r.test) continue;
    const l = by.get(r.test) ?? [];
    l.push(r);
    by.set(r.test, l);
  }
  const names = new Set([...tests, ...by.keys()]);
  return Array.from(names)
    .map((t) => {
      const h = (by.get(t) ?? []).sort((a, b) => b.created_at - a.created_at);
      return { test: t, run: h[0], history: h };
    })
    .sort((a, b) => {
      // failures first, then never run, then by name
      const rank = (x: TestRow) => (x.run?.status === "failed" || x.run?.status === "error" ? 0 : !x.run ? 1 : 2);
      return rank(a) - rank(b) || a.test.localeCompare(b.test);
    });
}

export function TestsView({ suite: suiteId }: { suite?: string }) {
  const { project } = useApp();
  const runs = useAsync(() => api.allRuns({ kind: "sim" }), []);
  useServerEvents((ev) => {
    if (ev.type === "run.created" && ev.run.kind === "sim") runs.setData((rs) => (rs ? [ev.run, ...rs] : rs));
    if (ev.type === "run.status") runs.setData((rs) => rs?.map((r) => (r.id === ev.run_id ? { ...r, status: ev.status } : r)));
  });
  const suites = useMemo(() => projectSuites(project), [project]);
  const suite = suites.find((s) => s.id === suiteId) ?? suites.find((s) => s.default) ?? suites[0];
  const bySuite = useMemo(() => {
    const m = new Map<string, RunSummary[]>();
    for (const r of runs.data ?? []) {
      const id = suiteOf(r, suites);
      if (id) m.set(id, [...(m.get(id) ?? []), r]);
    }
    return m;
  }, [runs.data, suites]);
  const rows = useMemo(() => latestPerTest(bySuite.get(suite.id) ?? [], suite.tests), [bySuite, suite]);
  const failing = rows.filter((r) => r.run?.status === "failed" || r.run?.status === "error").length;
  const passing = rows.filter((r) => r.run?.status === "passed").length;

  const cols: Column<TestRow>[] = [
    { key: "test", header: "Test", width: "minmax(200px, 3fr)", render: (r) => <span className="mono">{r.test}</span>, title: (r) => r.test, sortValue: (r) => r.test },
    { key: "status", header: "Latest", width: "104px", render: (r) => (r.run ? <StatusChip status={r.run.status} /> : <span className="faint small">never run</span>), sortValue: (r) => r.run?.status },
    {
      key: "reason",
      header: "Reason",
      width: "minmax(160px, 3fr)",
      render: (r) => <span className="muted">{r.run?.message ?? ""}</span>,
      title: (r) => r.run?.message ?? undefined,
    },
    { key: "hist", header: "Last 10", width: "110px", render: (r) => <History runs={r.history.slice(0, 10)} /> },
    { key: "dur", header: "Duration", width: "84px", align: "right", render: (r) => fmtDuration(r.run?.duration_s), sortValue: (r) => r.run?.duration_s },
    { key: "when", header: "When", width: "96px", align: "right", render: (r) => fmtAgo(r.run?.created_at), title: (r) => fmtDate(r.run?.created_at) },
    {
      key: "act",
      header: "",
      width: "272px",
      render: (r) =>
        r.run ? (
          <span className="row" style={{ gap: 6 }} onClick={(e) => e.stopPropagation()}>
            {r.run.status === "failed" ? (
              <DivergenceLink runId={r.run.id} run={r.run} label={suite.unit ? "Waves at first error" : "Waves at first mismatch"} unit={suite.unit} />
            ) : (
              <a href={href(`/waves/${r.run.id}`)}>waves</a>
            )}
            <a href={`#/runs/${r.run.id}`} style={{ flexShrink: 0 }}>details</a>
          </span>
        ) : (
          <span onClick={(e) => e.stopPropagation()}>
            <JobButton kind="sim" args={{ test: r.test, flow: suite.flow }}>
              Run
            </JobButton>
          </span>
        ),
    },
  ];

  if (runs.error) return <div className="view"><ErrorState error={runs.error} onRetry={runs.reload} /></div>;
  if (runs.loading && !runs.data) return <div className="view"><Loading what="Loading tests" /></div>;
  return (
    <div className="view fill">
      <div className="view-head">
        <h1>Tests</h1>
        {suites.length > 1 && <SuiteTabs suites={suites} current={suite.id} />}
        <span className="muted small">
          {fmtInt(rows.length)} tests · <span className="better">{fmtInt(passing)} passing</span> · <span className={failing ? "worse" : ""}>{fmtInt(failing)} failing</span>
        </span>
        <span className="spacer" />
        <JobButton key={suite.id} kind="sim-all" args={{ flow: suite.flow }} primary disabled={!suite.flow}>
          {suites.length > 1 ? `Run all ${suite.label.toLowerCase()}` : "Run all tests"}
        </JobButton>
      </div>
      {rows.length === 0 ? (
        suite.unit ? (
          <Empty title="No unit tests found" cmd={[`svkit sim --all --flow ${suite.flow}`]}>
            Each file matching <code>[flows.sim.{suite.flow}].testbench</code> in <code>svkit.toml</code> is one unit test.
          </Empty>
        ) : (
          <Empty title="No tests found" cmd={["svkit init", "svkit sim --all"]}>
            svkit lists tests from <code>[flows.sim.&lt;adapter&gt;].tests</code> in <code>svkit.toml</code>. Unit testbenches (one module each) go in a
            separate suite: <code>[flows.sim.unit]</code> with <code>testbench = "test/{"{test}"}_test.sv"</code>.
          </Empty>
        )
      ) : (
        <div style={{ flex: "1 1 auto", minHeight: 240, display: "flex" }}>
          <VirtualTable rows={rows} columns={cols} rowKey={(r) => r.test} onRowClick={(r) => r.run && navigate(`#/runs/${r.run.id}`)} ariaLabel="Tests" />
        </div>
      )}
    </div>
  );
}

export function SuiteTabs({ suites, current }: { suites: Suite[]; current: string }) {
  return (
    <span className="row" style={{ gap: 4 }}>
      <span className="seg" role="tablist" aria-label="Test suite">
        {suites.map((s) => (
          <button key={s.id} role="tab" aria-selected={s.id === current} className={s.id === current ? "on" : ""} onClick={() => navigate(`#/tests/${s.id}`, true)}>
            {s.label} <span className="muted">{fmtInt(s.tests.length)}</span>
          </button>
        ))}
      </span>
      <Info term={suites.find((s) => s.id === current)?.unit ? "unit_test" : "system_test"} />
    </span>
  );
}

function History({ runs }: { runs: RunSummary[] }) {
  // oldest → newest, left to right; glyph + color so it reads without color
  return (
    <span className="mono small" aria-label={runs.map((r) => r.status).join(", ")}>
      {runs
        .slice()
        .reverse()
        .map((r) => (
          <a key={r.id} href={`#/runs/${r.id}`} title={`${r.id} ${r.status} ${fmtDate(r.created_at)}`} className={r.status === "passed" ? "better" : r.status === "failed" || r.status === "error" ? "worse" : "faint"} style={{ textDecoration: "none" }}>
            {r.status === "passed" ? "●" : r.status === "failed" || r.status === "error" ? "✕" : "○"}
          </a>
        ))}
    </span>
  );
}
