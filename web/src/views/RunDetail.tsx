import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import { isSimResult, type Run, type SimResult } from "../api/types";
import { StatusChip } from "../components/StatusChip";
import { ErrorState, Loading } from "../components/States";
import { DASH, fmtDate, fmtDuration, fmtInt } from "../lib/format";
import { useAsync, useServerEvents } from "../lib/hooks";
import { href } from "../lib/router";
import { isSynthKind } from "./Runs";
import { DivergenceLink } from "./Tests";

const MAX_LOG = 5000;

export function TagEditor({ run, onChange }: { run: Run; onChange: (r: Run) => void }) {
  const [tag, setTag] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const add = async () => {
    const t = tag.trim();
    if (!t) return;
    try {
      onChange(await api.addTag(run.id, t));
      setTag("");
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    }
  };
  return (
    <div className="row wrap" style={{ gap: 4 }}>
      {run.tags.map((t) => (
        <span key={t} className="tag" title={t}>
          {t}
          <button aria-label={`remove tag ${t}`} onClick={async () => onChange(await api.removeTag(run.id, t))}>
            ✕
          </button>
        </span>
      ))}
      <input
        value={tag}
        onChange={(e) => setTag(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && add()}
        placeholder="add tag"
        aria-label="Add tag"
        maxLength={64}
        style={{ width: 110, height: 22 }}
      />
      {err && <span className="small worse">{err}</span>}
    </div>
  );
}

export function LogTail({ runId, live }: { runId: string; live: boolean }) {
  const log = useAsync(() => api.log(runId, 500), [runId]);
  const [lines, setLines] = useState<string[]>([]);
  const pre = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  useEffect(() => setLines(log.data?.lines ?? []), [log.data]);
  useServerEvents((ev) => {
    if (ev.type === "run.log" && ev.run_id === runId) setLines((ls) => (ls.length >= MAX_LOG ? [...ls.slice(-MAX_LOG + 1), ev.line] : [...ls, ev.line]));
  });
  useEffect(() => {
    const el = pre.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines]);
  if (log.error) return <ErrorState error={log.error} feature="Run log" />;
  return (
    <pre
      className="log"
      ref={pre}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
      aria-live={live ? "polite" : undefined}
    >
      {lines.length ? lines.join("\n") : log.loading ? "loading…" : "(empty log)"}
    </pre>
  );
}

export function RunDetail({ id }: { id: string }) {
  const run = useAsync(() => api.run(id), [id]);
  useServerEvents((ev) => {
    if (ev.type === "run.status" && ev.run_id === id) run.reload();
  });
  if (run.error) return <div className="view"><ErrorState error={run.error} onRetry={run.reload} /></div>;
  if (!run.data) return <div className="view"><Loading what={`Loading ${id}`} /></div>;
  const r = run.data;
  const sim = isSimResult(r.metrics) ? (r.metrics as SimResult) : null;
  const wavesArtifact = sim?.waves ?? Object.keys(r.artifacts).find((a) => /\.(vcd|fst)$/i.test(a));
  const live = r.status === "running" || r.status === "queued";
  const info: [string, React.ReactNode][] = [
    ["Kind", r.kind],
    ["Adapter", r.adapter],
    ["Test", r.test ?? DASH],
    ["Experiment", r.experiment ?? DASH],
    ["Created", fmtDate(r.created_at)],
    ["Duration", fmtDuration(r.duration_s)],
    ["Git", r.git_sha ? `${r.git_sha}${r.git_dirty ? " (dirty)" : ""}` : DASH],
    ["Exit code", r.exit_code ?? DASH],
    ["Group", r.group_id ? <a href={r.group_id && r.kind === "sweep-point" ? `#/sweep/${r.group_id}` : `#/runs`}>{r.group_id}</a> : DASH],
    ["Cache", r.cached_from ? <>hit of <a href={`#/runs/${r.cached_from}`}>{r.cached_from}</a></> : DASH],
    ["Knobs", Object.keys(r.knobs).length ? <span className="mono">{Object.entries(r.knobs).map(([k, v]) => `${k}=${v}`).join("  ")}</span> : DASH],
  ];
  return (
    <div className="view">
      <div className="view-head">
        <h1 className="mono">{r.id}</h1>
        <StatusChip status={r.status} />
        <span className="muted anywhere">{r.test ?? r.experiment ?? ""}</span>
        <span className="spacer" />
        {isSynthKind(r.kind) && (
          <a className="btn" href={`#/synth/${r.id}`}>
            Synthesis dashboard
          </a>
        )}
        {wavesArtifact && (
          <a className="btn" href={href(`/waves/${r.id}`)}>
            Open waves
          </a>
        )}
      </div>
      {r.message && <div className={`small anywhere ${r.status === "error" || r.status === "failed" ? "worse" : "muted"}`}>{r.message}</div>}
      {sim && (
        <div className="panel">
          <div className="panel-head">
            <h3>Result</h3>
            {sim.passed === true && <span className="chip good">✓ passed</span>}
            {sim.passed === false && <span className="chip bad">✕ failed</span>}
            {sim.passed == null && <span className="chip">undetermined</span>}
            <span className="spacer" />
            {sim.passed === false && <DivergenceLink runId={r.id} run={r} />}
          </div>
          <div className="panel-body small">
            {sim.reason && <div className="anywhere">{sim.reason}</div>}
            {sim.divergence && (
              <table className="t" style={{ marginTop: 6 }}>
                <tbody>
                  <tr>
                    <th>Line</th>
                    <td className="mono">{sim.divergence.line}</td>
                    <th>Time</th>
                    <td className="mono">{sim.divergence.time ?? DASH}</td>
                    <th>Cycle</th>
                    <td className="mono">{sim.divergence.cycle ?? DASH}</td>
                  </tr>
                  <tr>
                    <th>Expected</th>
                    <td colSpan={5} className="mono anywhere">{sim.divergence.expected ?? DASH}</td>
                  </tr>
                  <tr>
                    <th>Actual</th>
                    <td colSpan={5} className="mono anywhere worse">{sim.divergence.actual ?? DASH}</td>
                  </tr>
                </tbody>
              </table>
            )}
            {sim.perf && (
              <div className="muted" style={{ marginTop: 6 }}>
                cycles {fmtInt(sim.perf.cycles)} · instrs {fmtInt(sim.perf.instrs)}
              </div>
            )}
          </div>
        </div>
      )}
      <div className="grid2">
        <div className="panel">
          <div className="panel-head">
            <h3>Details</h3>
          </div>
          <div className="panel-body">
            <table className="t">
              <tbody>
                {info.map(([k, v]) => (
                  <tr key={k}>
                    <th style={{ width: 110, position: "static" }}>{k}</th>
                    <td className="anywhere">{v}</td>
                  </tr>
                ))}
                <tr>
                  <th style={{ position: "static" }}>Tags</th>
                  <td>
                    <TagEditor run={r} onChange={(nr) => run.setData(() => nr)} />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <div className="panel">
          <div className="panel-head">
            <h3>Artifacts</h3>
            <span className="muted small">{fmtInt(Object.keys(r.artifacts).length)}</span>
          </div>
          <div className="panel-body scroll" style={{ maxHeight: 300 }}>
            {Object.keys(r.artifacts).length === 0 ? (
              <span className="muted small">No artifacts kept for this run.</span>
            ) : (
              <ul style={{ margin: 0, paddingLeft: 16 }} className="small">
                {Object.keys(r.artifacts)
                  .sort()
                  .map((a) => (
                    <li key={a} className="anywhere">
                      <a className="mono" href={api.artifactUrl(r.id, a)} target="_blank" rel="noreferrer">
                        {a}
                      </a>
                    </li>
                  ))}
              </ul>
            )}
          </div>
        </div>
      </div>
      <div className="panel">
        <div className="panel-head">
          <h3>Log</h3>
          {live && <span className="chip run">● live</span>}
        </div>
        <LogTail runId={r.id} live={live} />
      </div>
    </div>
  );
}
