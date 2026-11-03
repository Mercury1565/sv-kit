import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { WaveScope, WaveSearchHit, WaveVar } from "../../api/types";
import { ErrorState } from "../../components/States";
import { useDebounced } from "../../lib/hooks";

export interface AddSignal {
  path: string;
  width: number;
  kind?: string;
}

const MAX_VARS_SHOWN = 500;

function ScopeNode({ run, name, path, depth, onAdd, initiallyOpen }: { run: string; name: string; path: string; depth: number; onAdd: (s: AddSignal[]) => void; initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(!!initiallyOpen);
  const [scope, setScope] = useState<WaveScope | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    if (!open || scope) return;
    api.waveScope(run, path).then(setScope, setErr);
  }, [open, scope, run, path]);
  const pad = 6 + depth * 12;
  const vars: WaveVar[] = scope?.vars ?? [];
  const shown = showAll ? vars : vars.slice(0, MAX_VARS_SHOWN);
  return (
    <>
      {path !== "" && (
        <div className="item" style={{ paddingLeft: pad }} onClick={() => setOpen(!open)} title={path} role="treeitem" aria-expanded={open}>
          <span className="twisty">{open ? "▾" : "▸"}</span>
          <span>{name}</span>
          {open && vars.length > 0 && (
            <button
              className="ghost"
              style={{ height: 18, padding: "0 4px", marginLeft: "auto", fontSize: 11 }}
              title={`Add all ${vars.length} signals of ${path}`}
              onClick={(e) => {
                e.stopPropagation();
                onAdd(vars.map((v) => ({ path: v.path, width: v.width ?? 64, kind: v.width === null ? "real" : v.kind })));
              }}
            >
              + all
            </button>
          )}
        </div>
      )}
      {open && err ? <div style={{ paddingLeft: pad + 14 }} className="small worse">failed to load scope</div> : null}
      {open &&
        scope?.scopes.map((s) => <ScopeNode key={s.path} run={run} name={s.name} path={s.path} depth={path === "" ? depth : depth + 1} onAdd={onAdd} initiallyOpen={path === "" && scope.scopes.length === 1} />)}
      {open &&
        shown.map((v) => (
          <div
            key={v.path}
            className="item"
            style={{ paddingLeft: pad + 14 }}
            onClick={() => onAdd([{ path: v.path, width: v.width ?? 64, kind: v.width === null ? "real" : v.kind }])}
            title={`${v.path}  [${v.width}] ${v.kind}${v.type ? " " + v.type : ""}\nclick to add`}
            role="treeitem"
          >
            <span className="var">{v.name}</span>
            {v.width !== null && v.width > 1 && <span className="w">[{v.width - 1}:0]</span>}
            {v.type && <span className="w">{v.type}</span>}
          </div>
        ))}
      {open && vars.length > shown.length && (
        <div className="item small muted" style={{ paddingLeft: pad + 14 }} onClick={() => setShowAll(true)}>
          show {vars.length - shown.length} more…
        </div>
      )}
    </>
  );
}

export function Hierarchy({ run, onAdd }: { run: string; onAdd: (s: AddSignal[]) => void }) {
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 150);
  const [hits, setHits] = useState<WaveSearchHit[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    if (!dq) {
      setHits(null);
      return;
    }
    let live = true;
    api.waveSearch(run, dq, 300).then(
      (h) => live && (setHits(h), setErr(null)),
      (e) => live && setErr(e),
    );
    return () => {
      live = false;
    };
  }, [dq, run]);
  return (
    <div className="waves-side">
      <div className="search">
        <input placeholder="Search signals (substring or glob)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search signals" />
      </div>
      <div className="tree" role="tree">
        {err ? <ErrorState error={err} feature="Signal search" /> : null}
        {hits ? (
          hits.length === 0 ? (
            <div className="small muted" style={{ padding: 8 }}>
              No signals match “{dq}”.
            </div>
          ) : (
            hits.map((h) => (
              <div key={h.path} className="item" style={{ paddingLeft: 8 }} onClick={() => onAdd([{ path: h.path, width: h.width ?? 64, kind: h.width === null ? "real" : h.kind }])} title={h.path}>
                <span className="var">{h.path}</span>
                {h.width !== null && h.width > 1 && <span className="w">[{h.width - 1}:0]</span>}
              </div>
            ))
          )
        ) : (
          <ScopeNode run={run} name="" path="" depth={0} onAdd={onAdd} initiallyOpen />
        )}
      </div>
    </div>
  );
}
