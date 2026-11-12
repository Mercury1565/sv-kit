import { useState } from "react";
import { api } from "../api/client";
import type { CompareResult, MetricDelta } from "../api/types";
import { Empty, ErrorState, Loading } from "../components/States";
import { compareCell, compareCsv, compareRows, withBaseline, type CompareLevel } from "../lib/compare";
import { readTheme } from "../components/EChart";
import { downloadDataUrl, downloadText, fileSlug } from "../lib/download";
import { tablePng, type ImgCell, type TableImage } from "../lib/tableImage";
import { metricLabel } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { Info } from "../components/Info";
import { metricTerm } from "../lib/glossary";
import { navigate } from "../lib/router";

const LEVEL_KEY = "svkit.compare.level";

function loadLevel(): CompareLevel {
  try {
    return localStorage.getItem(LEVEL_KEY) === "advanced" ? "advanced" : "basic";
  } catch {
    return "basic";
  }
}

/** The rows shown, laid out for the PNG: metric name, then each run's value over its change. */
export function compareImage(cmp: CompareResult, metrics: MetricDelta[], level: CompareLevel): TableImage {
  const header: ImgCell[] = [
    [{ text: "Metric", bold: true }],
    ...cmp.run_ids.map((id, i): ImgCell => [
      { text: cmp.labels[i] || id, bold: true, tone: "fg", mono: !cmp.labels[i] || cmp.labels[i] === id },
      ...(cmp.labels[i] && cmp.labels[i] !== id
        ? [{ text: i === 0 ? `${id} · baseline` : id, mono: true, tone: "muted" as const }]
        : i === 0
          ? [{ text: "baseline", tone: "muted" as const }]
          : []),
    ]),
  ];
  const rows = metrics.map((m): ImgCell[] => [
    [
      { text: `${metricLabel(m.metric)}${m.unit ? ` (${m.unit})` : ""}`, bold: true },
      ...(m.better !== "none" ? [{ text: m.better === "higher" ? "↑ better" : "↓ better", tone: "faint" as const }] : []),
    ],
    ...m.values.map((_, i): ImgCell => {
      const c = compareCell(m, i);
      const tone = c.verdict === "better" ? "good" : c.verdict === "worse" ? "bad" : "muted";
      return [{ text: c.value }, ...(c.delta !== null ? [{ text: `${c.glyph ? `${c.glyph} ` : ""}${c.delta}${c.pct ? ` (${c.pct})` : ""}`, tone } as const] : [])];
    }),
  ]);
  return {
    title: `Synthesis compare · ${cmp.run_ids.length} runs`,
    subtitle: `${level === "basic" ? "Basic" : "Advanced"} view · first column is the baseline · ▲ better ▼ worse`,
    header,
    rows,
  };
}

export function CompareTable({ cmp, onBaseline, metrics = cmp.metrics }: { cmp: CompareResult; onBaseline?: (idx: number) => void; metrics?: MetricDelta[] }) {
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
          {metrics.map((m) => (
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
                        <span className="sr-only">
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
  const [level, setLevelState] = useState<CompareLevel>(loadLevel);
  const setLevel = (l: CompareLevel) => {
    setLevelState(l);
    try {
      localStorage.setItem(LEVEL_KEY, l);
    } catch {
      /* private window: not remembered */
    }
  };
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
        <CompareBody cmp={cmp.data} ids={ids} level={level} setLevel={setLevel} />
      )}
    </div>
  );
}

function CompareBody({ cmp, ids, level, setLevel }: { cmp: CompareResult; ids: string[]; level: CompareLevel; setLevel: (l: CompareLevel) => void }) {
  const rows = compareRows(cmp, level);
  const hidden = cmp.metrics.length - compareRows(cmp, "basic").length;
  const base = `compare-${cmp.run_ids.map(fileSlug).join("-").slice(0, 80)}-${level}`;
  const [error, setError] = useState<string | null>(null);
  const saveImage = () => {
    setError(null);
    try {
      const t = readTheme();
      const cs = getComputedStyle(document.documentElement);
      downloadDataUrl(`${base}.png`, tablePng(compareImage(cmp, rows, level), { ...t, font: cs.getPropertyValue("--font").trim() || "sans-serif" }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <span className="seg" role="radiogroup" aria-label="How much to compare">
          {(["basic", "advanced"] as CompareLevel[]).map((l) => (
            <button
              key={l}
              role="radio"
              aria-checked={level === l}
              className={level === l ? "on" : ""}
              onClick={() => setLevel(l)}
              title={l === "basic" ? "Timing, clock period, area, power and cell count" : "Also cells per transistor speed class and area per block"}
            >
              {l === "basic" ? "Basic" : "Advanced"}
            </button>
          ))}
        </span>
        <span className="muted small">
          {level === "basic"
            ? hidden > 0
              ? `${hidden} more ${hidden === 1 ? "row" : "rows"} in Advanced`
              : "timing, area, power and cells"
            : `all ${cmp.metrics.length} rows`}
        </span>
        <span className="spacer" />
        <button onClick={saveImage} disabled={!rows.length} title="Save this table, as shown, as a PNG image">
          Save image (PNG)
        </button>
        <button onClick={() => downloadText(`${base}.csv`, compareCsv(cmp, rows), "text/csv")} disabled={!rows.length} title="Download the rows shown as CSV: each run's value, change, change % and verdict">
          Download CSV
        </button>
      </div>
      {error && <div className="small worse">Could not save the image: {error}</div>}
      {rows.length ? (
        <CompareTable cmp={cmp} metrics={rows} onBaseline={(i) => navigate(`/compare?ids=${withBaseline(ids, i).join(",")}`)} />
      ) : (
        <Empty title="None of these runs reported timing, area, power or cell counts">
          {hidden > 0 ? <>Switch to <b>Advanced</b> to see the other {hidden} rows.</> : "Check the runs' logs in Synthesis."}
        </Empty>
      )}
    </>
  );
}
