import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { XCause, XTraceNode } from "../api/types";
import { SourcePanel } from "../components/SourcePanel";
import { Empty, ErrorState, Loading } from "../components/States";
import { fmtInt } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { href, navigate } from "../lib/router";

export const CAUSE_LABEL: Record<XCause, string> = {
  no_reset: "register never reset / assigned",
  uninit_memory: "uninitialized memory read",
  index_out_of_range: "index out of range",
  multi_driver: "multiple drivers",
  external_input: "X/Z from testbench / top port",
  latch: "latch holding X",
  unknown: "unresolved (bound hit)",
};

export function collectRootCauses(n: XTraceNode, out: XTraceNode[] = []): XTraceNode[] {
  if (n.cause) out.push(n);
  n.children.forEach((c) => collectRootCauses(c, out));
  return out;
}

interface TreeProps {
  node: XTraceNode;
  run: string;
  onSource?: (file: string, line: number) => void;
}

function NodeView({ node, run, onSource, depth }: TreeProps & { depth: number }) {
  const [open, setOpen] = useState(depth < 12);
  const rootCause = !!node.cause && node.cause !== "unknown";
  const d = node.driver;
  return (
    <li>
      <div className={`xt-node ${rootCause ? "root-cause" : ""}`} data-testid="xt-node">
        {node.children.length > 0 ? (
          <button className="ghost icon" style={{ height: 18 }} onClick={() => setOpen(!open)} aria-expanded={open} aria-label={open ? "collapse" : "expand"}>
            {open ? "▾" : "▸"}
          </button>
        ) : (
          <span style={{ width: 22 }} />
        )}
        <span className="xt-sig">{node.signal}</span>
        <span className="xt-val">{node.value}</span>
        <span className="xt-meta small">
          <span>@ {fmtInt(node.time)}</span>
          {d && <span className="chip">{d.kind}</span>}
          {d?.file && (
            <a
              className="mono"
              href="#"
              onClick={(e) => {
                e.preventDefault();
                onSource?.(d.file!, d.line ?? 1);
              }}
              title="Show source"
            >
              {d.file}
              {d.line ? `:${d.line}` : ""}
            </a>
          )}
          <a href={href(`/waves/${run}`, { t: node.time, signals: node.signal })}>waves at t</a>
        </span>
        {node.cause && (
          <span className={`chip ${rootCause ? "bad" : "warn"}`} title={node.cause}>
            {rootCause ? "◆ root cause: " : ""}
            {CAUSE_LABEL[node.cause] ?? node.cause}
          </span>
        )}
        {node.note && <span className="xt-note small">{node.note}</span>}
        {d?.text && <span className="xt-text small">{d.text}</span>}
      </div>
      {open && node.children.length > 0 && (
        <ul>
          {node.children.map((c, i) => (
            <NodeView key={`${c.signal}@${c.time}#${i}`} node={c} run={run} onSource={onSource} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

export function XTraceTree(props: TreeProps) {
  return (
    <div className="xt" role="tree">
      <ul>
        <NodeView {...props} depth={0} />
      </ul>
    </div>
  );
}

export default function XTraceView({ query }: { query: URLSearchParams }) {
  const qRun = query.get("run") ?? "";
  const qSig = query.get("signal") ?? "";
  const qTime = query.get("time") ?? "";
  const qDepth = query.get("depth") ?? "20";
  const [run, setRun] = useState(qRun);
  const [signal, setSignal] = useState(qSig);
  const [time, setTime] = useState(qTime);
  const [depth, setDepth] = useState(qDepth);
  const [src, setSrc] = useState<{ file: string; line: number } | null>(null);
  useEffect(() => {
    setRun(qRun);
    setSignal(qSig);
    setTime(qTime);
    setDepth(qDepth);
  }, [qRun, qSig, qTime, qDepth]);

  const simRuns = useAsync(() => api.runs({ kind: "sim", limit: 200 }), []);
  const ready = !!(qRun && qSig && qTime !== "");
  const trace = useAsync(
    () => (ready ? api.xtrace({ run: qRun, signal: qSig, time: Number(qTime), depth: Number(qDepth) || 20 }) : Promise.resolve(null)),
    [qRun, qSig, qTime, qDepth],
  );
  const causes = useMemo(() => (trace.data ? collectRootCauses(trace.data) : []), [trace.data]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    navigate(href("/xtrace", { run, signal: signal.trim(), time, depth }));
  };

  return (
    <div className="view" style={{ flexDirection: "row", alignItems: "flex-start", gap: 12 }}>
      <div className="grow" style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
        <div className="view-head">
          <h1>X-Trace</h1>
          <span className="muted small">Where did this X come from?</span>
        </div>
        <form className="row wrap" onSubmit={submit} style={{ gap: 6 }}>
          <label className="field">
            run
            <input list="xt-runs" className="mono" value={run} onChange={(e) => setRun(e.target.value)} placeholder="r_0001" style={{ width: 100 }} required />
            <datalist id="xt-runs">
              {simRuns.data?.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.test ?? ""} {r.status}
                </option>
              ))}
            </datalist>
          </label>
          <label className="field grow">
            signal
            <input className="mono grow" value={signal} onChange={(e) => setSignal(e.target.value)} placeholder="tb.dut.u_core.wb_data" required />
          </label>
          <label className="field">
            time
            <input className="mono" type="number" value={time} onChange={(e) => setTime(e.target.value)} style={{ width: 110 }} required />
          </label>
          <label className="field">
            depth
            <input type="number" min={1} max={200} value={depth} onChange={(e) => setDepth(e.target.value)} style={{ width: 60 }} />
          </label>
          <button className="primary" type="submit">
            Trace
          </button>
        </form>
        {!ready ? (
          <Empty title="Trace an X back to its root cause" cmd="svkit xtrace tb.dut.u_core.wb_data @4175">
            Click an X value in <a href="#/waves">Waves</a> or fill in run, signal and time above.
          </Empty>
        ) : trace.error ? (
          <ErrorState error={trace.error} feature="The X tracer" onRetry={trace.reload} />
        ) : trace.loading || !trace.data ? (
          <Loading what="Tracing" />
        ) : (
          <>
            <div className="panel">
              <div className="panel-head">
                <h3>Root causes</h3>
                <span className="muted small">
                  {causes.filter((c) => c.cause !== "unknown").length}
                  {causes.some((c) => c.cause === "unknown") ? ` · ${causes.filter((c) => c.cause === "unknown").length} branch(es) stopped at a bound` : ""}
                </span>
              </div>
              <div className="panel-body small">
                {causes.length === 0 ? (
                  <span className="muted">No root cause classified within depth {qDepth}. The leaves below are where the trace stopped.</span>
                ) : (
                  <ul style={{ margin: 0, paddingLeft: 16 }}>
                    {causes.map((c, i) => (
                      <li key={i} className="anywhere">
                        <b>{CAUSE_LABEL[c.cause!] ?? c.cause}</b> — <span className="mono">{c.signal}</span> @ {fmtInt(c.time)}
                        {c.note ? <span className="muted"> · {c.note}</span> : null}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
            <XTraceTree node={trace.data} run={qRun} onSource={(file, line) => setSrc({ file, line })} />
          </>
        )}
      </div>
      {src && (
        <div style={{ width: 460, maxWidth: "45%", height: "calc(100vh - 80px)", position: "sticky", top: 0, flexShrink: 0 }}>
          <SourcePanel file={src.file} line={src.line} onClose={() => setSrc(null)} />
        </div>
      )}
    </div>
  );
}
