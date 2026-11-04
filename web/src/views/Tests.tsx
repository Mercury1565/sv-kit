import { useMemo, useState } from "react";
import { api, ApiError } from "../api/client";
import type { RunSummary } from "../api/types";
import { JobButton } from "../components/JobButton";
import { StatusChip } from "../components/StatusChip";
import { Empty, ErrorState, Loading } from "../components/States";
import { VirtualTable, type Column } from "../components/VirtualTable";
import { useApp } from "../lib/appState";
import { fmtAgo, fmtDate, fmtDuration, fmtInt } from "../lib/format";
import { useAsync, useServerEvents } from "../lib/hooks";
import { href, navigate } from "../lib/router";

/** Why a failed run has no divergence to jump to, in words the user can act on. */
export function noDivergenceReason(run?: Pick<RunSummary, "message" | "artifacts" | "cached_from">): string {
  const why = run?.message ? ` (${run.message})` : "";
  if (run && !Object.keys(run.artifacts ?? {}).some((n) => /^waves\./.test(n)))
    return `no divergence: the test failed before producing output to compare${why}. Open "details" for the log, then run it again.`;
  return `no divergence: the output matched or was not compared; the failure came from elsewhere${why}.`;
}

/** Button: ask the backend for the first divergence and open the waveform there. */
export function DivergenceLink({ runId, run, label = "Open waves at divergence" }: { runId: string; run?: RunSummary; label?: string }) {
  const [state, setState] = useState<"idle" | "busy" | string>("idle");
  const hasWaves = run ? Object.keys(run.artifacts ?? {}).some((n) => /^waves\./.test(n)) : true;
  const go = async () => {
    setState("busy");
    try {
      const d = await api.divergence(runId);
      if (d.time === null || d.time === undefined) {
        setState(`divergence at output line ${d.line} could not be mapped to a sim time`);
        navigate(href(`/waves/${runId}`));
        return;
      }
      navigate(href(`/waves/${runId}`, { t: d.time }));
    } catch (e) {
      if (e instanceof ApiError && e.notAvailable) {
        setState(noDivergenceReason(run));
        if (hasWaves) navigate(href(`/waves/${runId}`));
      } else setState(String(e));
    }
  };
  return (
    <span className="row" style={{ gap: 6, minWidth: 0 }}>
      <button onClick={go} disabled={state === "busy"}>
        {label}
      </button>
      {state !== "idle" && state !== "busy" && <span className="small muted ellipsis" title={state}>{state}</span>}
    </span>
  );
}

export interface TestRow {
  test: string;
  run?: RunSummary;
  history: RunSummary[]; // newest first
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

export function TestsView() {
  const { project } = useApp();
  const runs = useAsync(() => api.allRuns({ kind: "sim" }), []);
  useServerEvents((ev) => {
    if (ev.type === "run.created" && ev.run.kind === "sim") runs.setData((rs) => (rs ? [ev.run, ...rs] : rs));
    if (ev.type === "run.status") runs.setData((rs) => rs?.map((r) => (r.id === ev.run_id ? { ...r, status: ev.status } : r)));
  });
  const rows = useMemo(() => latestPerTest(runs.data ?? [], project?.tests ?? []), [runs.data, project]);
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
      width: "220px",
      render: (r) =>
        r.run ? (
          <span className="row" style={{ gap: 6 }} onClick={(e) => e.stopPropagation()}>
            {r.run.status === "failed" ? (
              <DivergenceLink runId={r.run.id} run={r.run} label="Waves at divergence" />
            ) : (
              <a href={href(`/waves/${r.run.id}`)}>waves</a>
            )}
            <a href={`#/runs/${r.run.id}`}>details</a>
          </span>
        ) : (
          <span onClick={(e) => e.stopPropagation()}>
            <JobButton kind="sim" args={{ test: r.test }}>
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
        <span className="muted small">
          {fmtInt(rows.length)} tests · <span className="better">{fmtInt(passing)} passing</span> · <span className={failing ? "worse" : ""}>{fmtInt(failing)} failing</span>
        </span>
        <span className="spacer" />
        <JobButton kind="sim-all" primary disabled={!project?.flows.sim.length}>
          Run all tests
        </JobButton>
      </div>
      {rows.length === 0 ? (
        <Empty title="No tests found" cmd={["svkit init", "svkit sim --all"]}>
          svkit lists tests from <code>[flows.sim.&lt;adapter&gt;].tests</code> in <code>svkit.toml</code>.
        </Empty>
      ) : (
        <div style={{ flex: "1 1 auto", minHeight: 240, display: "flex" }}>
          <VirtualTable rows={rows} columns={cols} rowKey={(r) => r.test} onRowClick={(r) => r.run && navigate(`#/runs/${r.run.id}`)} ariaLabel="Tests" />
        </div>
      )}
    </div>
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
