import { api } from "../api/client";
import type { CompareResult } from "../api/types";
import { Empty, ErrorState, Loading } from "../components/States";
import { compareCell, withBaseline } from "../lib/compare";
import { metricLabel } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { Info } from "../components/Info";
import { metricTerm } from "../lib/glossary";
import { navigate } from "../lib/router";

export function CompareTable({ cmp, onBaseline }: { cmp: CompareResult; onBaseline?: (idx: number) => void }) {
  return (
    <div className="scroll panel">
      <table className="t" aria-label="Run comparison">
        <thead>
          <tr>
            <th>Metric</th>
            {cmp.run_ids.map((id, i) => (
              <th key={id} className="num" style={{ minWidth: 120, maxWidth: 240 }}>
                <div className="ellipsis" title={cmp.labels[i]} style={{ maxWidth: 240 }}>
                  {cmp.labels[i]}
                </div>
                <div className="row" style={{ justifyContent: "flex-end", gap: 6, fontWeight: 400 }}>
                  <a className="mono" href={`#/synth/${id}`}>
                    {id}
                  </a>
                  {i === 0 ? (
                    <span className="chip">baseline</span>
                  ) : onBaseline ? (
                    <button className="ghost" style={{ height: 18, padding: "0 4px", fontSize: 11 }} onClick={() => onBaseline(i)}>
                      make baseline
                    </button>
                  ) : null}
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cmp.metrics.map((m) => (
            <tr key={m.metric}>
              <th style={{ position: "static", background: "transparent", color: "var(--fg)", fontWeight: 500 }}>
                {metricLabel(m.metric)}
                {m.unit ? <span className="faint"> ({m.unit})</span> : null}
                {metricTerm(m.metric) && <> <Info term={metricTerm(m.metric)!} /></>}
                {m.better !== "none" && <span className="faint small"> {m.better === "higher" ? "↑ better" : "↓ better"}</span>}
              </th>
              {m.values.map((_, i) => {
                const c = compareCell(m, i);
                const cls = c.verdict === "better" ? "better" : c.verdict === "worse" ? "worse" : "";
                return (
                  <td key={i} className="num" title={c.title}>
                    <div>{c.value}</div>
                    {c.delta !== null && (
                      <div className={`delta ${cls}`}>
                        {c.glyph && <span aria-hidden>{c.glyph} </span>}
                        {c.delta}
                        {c.pct ? ` (${c.pct})` : ""}
                        <span className="sr-only" style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>
                          {c.verdict}
                        </span>
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function CompareView({ ids }: { ids: string[] }) {
  const key = ids.join(",");
  const cmp = useAsync(() => (ids.length ? api.compare(ids) : Promise.resolve(null)), [key]);
  if (ids.length < 2)
    return (
      <div className="view">
        <Empty title="Pick two or more synthesis runs to compare" cmd="svkit synth --matrix">
          Select runs with the checkboxes in <a href="#/synth">Synthesis</a> or <a href="#/runs">Runs</a>.
        </Empty>
      </div>
    );
  return (
    <div className="view">
      <div className="view-head">
        <h1>Compare</h1>
        <span className="muted small">{ids.length} runs · first column is the baseline · ▲ better ▼ worse</span>
      </div>
      {cmp.error ? (
        <ErrorState error={cmp.error} feature="Run comparison" onRetry={cmp.reload} />
      ) : !cmp.data ? (
        <Loading what="Comparing" />
      ) : (
        <CompareTable cmp={cmp.data} onBaseline={(i) => navigate(`/compare?ids=${withBaseline(ids, i).join(",")}`)} />
      )}
    </div>
  );
}
