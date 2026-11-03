import { useEffect, useRef } from "react";
import { api } from "../api/client";
import { useAsync } from "../lib/hooks";
import { ErrorState, Loading } from "./States";

/** Read-only source excerpt around `line` (from /api/source), the target line highlighted. */
export function SourcePanel({ file, line, onClose, context = 30 }: { file: string; line: number; onClose?: () => void; context?: number }) {
  const src = useAsync(() => api.source(file, line, context), [file, line, context]);
  const hl = useRef<HTMLDivElement>(null);
  useEffect(() => {
    hl.current?.scrollIntoView({ block: "center" });
  }, [src.data]);
  return (
    <div className="panel" style={{ display: "flex", flexDirection: "column", minHeight: 0, height: "100%" }}>
      <div className="panel-head">
        <span className="mono small ellipsis grow" title={`${file}:${line}`} style={{ direction: "rtl", textAlign: "left" }}>
          {file}:{line}
        </span>
        {onClose && (
          <button className="ghost icon" onClick={onClose} aria-label="Close source">
            ✕
          </button>
        )}
      </div>
      <div className="src grow" style={{ minHeight: 0 }}>
        {src.loading && <Loading what="Loading source" />}
        {src.error ? <ErrorState error={src.error} feature="Source view" /> : null}
        {src.data?.lines.map((l, i) => {
          const n = src.data!.start + i;
          return (
            <div key={n} className={`ln ${n === line ? "hl" : ""}`} ref={n === line ? hl : undefined}>
              <span className="n">{n}</span>
              <span className="c">{l}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
