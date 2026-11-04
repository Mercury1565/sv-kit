import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import type { RunSummary, WaveDecode, WaveMeta } from "../../api/types";
import { SourcePanel } from "../../components/SourcePanel";
import { StatusChip } from "../../components/StatusChip";
import { Empty, ErrorState, Loading } from "../../components/States";
import { fmtAgo } from "../../lib/format";
import { useAsync } from "../../lib/hooks";
import { href, navigate, replaceQuietly } from "../../lib/router";
import { defaultRadix, formatTime, formatValue, hasX, hasZ, pickUnit, valueAt, xToT, type Radix, type View } from "../../lib/waves";
import { Hierarchy, type AddSignal } from "./Hierarchy";
import { locateSignal } from "./locate";
import { ROW_H, WaveRenderer, type RenderRow } from "./renderer";

interface Sig {
  path: string;
  width: number;
  kind?: string;
  radix: Radix;
  decode?: WaveDecode | null;
  expanded: boolean;
}

export default function WavesView({ runId, query }: { runId?: string; query: URLSearchParams }) {
  if (!runId) return <WavePicker />;
  return <WaveSession key={runId} runId={runId} query={query} />;
}

// ------------------------------------------------------------------ picker
function WavePicker() {
  const runs = useAsync(() => api.runs({ kind: "sim", limit: 500 }), []);
  const withWaves = (runs.data ?? []).filter((r) => Object.keys(r.artifacts).some((a) => /\.(vcd|fst)$/i.test(a)));
  return (
    <div className="view">
      <div className="view-head">
        <h1>Waves</h1>
        <span className="muted small">Pick a simulation run</span>
      </div>
      {runs.error ? (
        <ErrorState error={runs.error} onRetry={runs.reload} />
      ) : !runs.data ? (
        <Loading />
      ) : withWaves.length === 0 ? (
        <Empty title="No waveforms yet" cmd="svkit sim <test>">
          Sim runs keep their VCD/FST dump as an artifact; set <code>waves</code> in <code>[flows.sim.&lt;adapter&gt;]</code>.
        </Empty>
      ) : (
        <div className="panel scroll" style={{ maxHeight: "calc(100vh - 140px)" }}>
          <table className="t">
            <thead>
              <tr>
                <th>Run</th>
                <th>Test</th>
                <th>Status</th>
                <th>Adapter</th>
                <th className="num">When</th>
              </tr>
            </thead>
            <tbody>
              {withWaves.map((r: RunSummary) => (
                <tr key={r.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/waves/${r.id}`)}>
                  <td className="mono">
                    <a href={`#/waves/${r.id}`}>{r.id}</a>
                  </td>
                  <td className="mono anywhere">{r.test}</td>
                  <td>
                    <StatusChip status={r.status} />
                  </td>
                  <td>{r.adapter}</td>
                  <td className="num">{fmtAgo(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ session
function toRows(sigs: Sig[]): RenderRow[] {
  const rows: RenderRow[] = [];
  for (const s of sigs) {
    const real = s.kind === "real" || s.kind === "realtime" || s.width === null;
    rows.push({ key: s.path, path: s.path, width: s.width, radix: s.radix, enumMap: s.decode?.enum ?? null, real });
    if (s.expanded && s.decode?.kind === "struct" && s.decode.fields) {
      for (const f of s.decode.fields) {
        const w = f.msb - f.lsb + 1;
        rows.push({
          key: `${s.path}#${f.name}`,
          path: s.path,
          width: w,
          radix: f.enum ? "enum" : defaultRadix(w, false),
          enumMap: f.enum ?? null,
          field: { msb: f.msb, lsb: f.lsb, parentWidth: s.width },
        });
      }
    }
  }
  return rows;
}

const RADIXES: Radix[] = ["hex", "bin", "dec", "signed", "enum"];
const AUTO_MAX = 32;   // signals added by `auto=top`

function WaveSession({ runId, query }: { runId: string; query: URLSearchParams }) {
  const meta = useAsync(() => api.waveMeta(runId), [runId]);
  if (meta.error) return <div className="view"><ErrorState error={meta.error} feature="Waveforms for this run" onRetry={meta.reload} /></div>;
  if (!meta.data) return <div className="view"><Loading what="Opening waveform" /></div>;
  return <WaveWorkspace runId={runId} meta={meta.data} query={query} />;
}

function WaveWorkspace({ runId, meta, query }: { runId: string; meta: WaveMeta; query: URLSearchParams }) {
  const qt = query.get("t");
  const qSignals = query.get("signals");
  const [sigs, setSigs] = useState<Sig[]>([]);
  const [cursor, setCursor] = useState<number | null>(qt !== null && qt !== "" && Number.isFinite(Number(qt)) ? Number(qt) : null);
  const [marker, setMarker] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<View>({ t0: meta.t_start, t1: meta.t_end, width: 1 });
  const [scrollTop, setScrollTop] = useState(0);
  const [gridH, setGridH] = useState(400);
  const [dataVer, setDataVer] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [xPop, setXPop] = useState<{ x: number; y: number; signal: string; t: number } | null>(null);
  const [src, setSrc] = useState<{ file: string; line: number } | null>(null);
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [dropIdx, setDropIdx] = useState<number | null>(null);

  const gridRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const axisRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const rendRef = useRef<WaveRenderer | null>(null);

  const rows = useMemo(() => toRows(sigs), [sigs]);
  const ts = meta.timescale;

  // ---------------------------------------------------------------- renderer lifecycle
  useLayoutEffect(() => {
    const r = new WaveRenderer(canvasRef.current!, axisRef.current!, (signals, t0, t1, max_changes) => api.waveData(runId, { signals, t0: Math.floor(t0), t1: Math.ceil(t1), max_changes }), {
      onViewChange: (() => {
        let pending = false;
        return (v: View) => {
          if (pending) return;
          pending = true;
          setTimeout(() => {
            pending = false;
            setView({ ...r.view });
          }, 80);
          void v;
        };
      })(),
      onData: () => setDataVer((n) => n + 1),
      onWidth: (path, width) => setSigs((ss) => (ss.some((s) => s.path === path && s.width !== width) ? ss.map((s) => (s.path === path ? { ...s, width, radix: s.width ? s.radix : defaultRadix(width, s.radix === "enum") } : s)) : ss)),
      onError: (e) => setStatus(`data request failed: ${e instanceof Error ? e.message : String(e)}`),
    });
    rendRef.current = r;
    r.setBounds(meta.t_start, meta.t_end, ts);
    const init = cursor;
    const wrap = wrapRef.current!;
    const grid = gridRef.current!;
    const size = () => {
      r.resize(wrap.clientWidth, grid.clientHeight);
      setGridH(grid.clientHeight);
    };
    size();
    if (init !== null) {
      const total = meta.t_end - meta.t_start;
      const span = Math.max(Math.min(total, 400), total / 250);
      r.setView(init - span / 2, init + span / 2);
    } else r.fit();
    setView({ ...r.view });
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(size) : null;
    ro?.observe(wrap);
    ro?.observe(grid);
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onMq = () => r.refreshTheme();
    mq?.addEventListener?.("change", onMq);
    return () => {
      ro?.disconnect();
      mq?.removeEventListener?.("change", onMq);
      r.destroy();
      rendRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, meta]);

  useEffect(() => rendRef.current?.setRows(rows), [rows]);
  useEffect(() => rendRef.current?.setCursor(cursor, marker, selected), [cursor, marker, selected]);

  // ---------------------------------------------------------------- signals
  const addSignals = useCallback(
    async (adds: AddSignal[]) => {
      setSigs((ss) => {
        const have = new Set(ss.map((s) => s.path));
        const fresh = adds.filter((a) => !have.has(a.path)).map((a) => ({ path: a.path, width: a.width, kind: a.kind, radix: defaultRadix(a.width, false), expanded: false }));
        return [...ss, ...fresh];
      });
      if (adds.length === 1) setSelected(adds[0].path);
      // type decoding (enum names / struct fields) from the design; optional
      for (const a of adds.slice(0, 64)) {
        api.waveDecode(runId, a.path).then(
          (d) => setSigs((ss) => ss.map((s) => (s.path === a.path ? { ...s, decode: d, radix: d.kind === "enum" ? "enum" : s.radix } : s))),
          () => {},
        );
      }
    },
    [runId],
  );

  // signals from the URL (links from tests / X-trace)
  useEffect(() => {
    if (!qSignals) return;
    const paths = qSignals.split(",").map((s) => s.trim()).filter(Boolean);
    if (!paths.length) return;
    const t = cursor ?? meta.t_start;
    api.waveData(runId, { signals: paths, t0: t, t1: t, max_changes: 1 }).then(
      (d) =>
        addSignals(
          paths.map((p) => {
            const w = d.signals[p]?.width;
            // width null = real-valued signal (ARCHITECTURE §6)
            return w === null ? { path: p, width: 64, kind: "real" } : { path: p, width: w ?? 1 };
          }),
        ),
      () => addSignals(paths.map((p) => ({ path: p, width: 1 }))),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qSignals, runId]);

  // `auto=top` (links to a first error / mismatch): start with the top scope's own signals,
  // e.g. a unit testbench's DUT ports, instead of an empty list
  const qAuto = query.get("auto");
  useEffect(() => {
    if (qSignals || qAuto !== "top") return;
    let off = false;
    (async () => {
      const root = await api.waveScope(runId, "");
      const top = root.vars.length ? root : root.scopes.length ? await api.waveScope(runId, root.scopes[0].path) : null;
      if (off || !top) return;
      addSignals(top.vars.slice(0, AUTO_MAX).map((v) => (v.width === null ? { path: v.path, width: 64, kind: "real" } : { path: v.path, width: v.width })));
    })().catch(() => {});
    return () => {
      off = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qAuto, runId]);

  // external navigation to a new cursor time
  useEffect(() => {
    if (qt === null || qt === "") return;
    const t = Number(qt);
    if (!Number.isFinite(t)) return;
    setCursor(t);
    rendRef.current?.reveal(t);
  }, [qt]);

  // keep the URL in sync (quietly: no history spam, no re-route)
  useEffect(() => {
    const id = setTimeout(() => {
      replaceQuietly(href(`/waves/${runId}`, { t: cursor === null ? null : Math.round(cursor), signals: sigs.map((s) => s.path).join(",") }));
    }, 250);
    return () => clearTimeout(id);
  }, [cursor, sigs, runId]);

  const removeSig = (path: string) => setSigs((ss) => ss.filter((s) => s.path !== path));
  const updSig = (path: string, f: (s: Sig) => Sig) => setSigs((ss) => ss.map((s) => (s.path === path ? f(s) : s)));

  // ---------------------------------------------------------------- navigation
  const selRow = rows.find((r) => r.key === selected) ?? null;
  const jump = useCallback(
    async (what: "change" | "x", dir: 1 | -1) => {
      if (!selRow) {
        setStatus("select a signal first");
        return;
      }
      const from = cursor ?? view.t0;
      try {
        const r = await api.waveNext(runId, selRow.path, Math.round(from), what, dir);
        if (r.t === null || r.t === undefined) setStatus(`no ${what === "x" ? "X" : "change"} ${dir > 0 ? "after" : "before"} ${formatTime(from, ts)}`);
        else {
          setStatus(null);
          setCursor(r.t);
          rendRef.current?.reveal(r.t);
        }
      } catch (e) {
        setStatus(`navigation unavailable: ${e instanceof Error ? e.message : String(e)}`);
      }
    },
    [selRow, cursor, view.t0, runId, ts],
  );

  const goEdge = (edge: -1 | 1) => {
    setStatus(null);
    setCursor(edge < 0 ? meta.t_start : meta.t_end);
    rendRef.current?.toEdge(edge);
  };

  const onKey = (e: React.KeyboardEvent) => {
    const r = rendRef.current;
    if (!r) return;
    const idx = rows.findIndex((x) => x.key === selected);
    switch (e.key) {
      case "+":
      case "=":
        r.zoom(0.5);
        break;
      case "-":
      case "_":
        r.zoom(2);
        break;
      case "f":
      case "F":
        r.fit();
        break;
      case "ArrowRight":
      case "n":
        jump("change", 1);
        break;
      case "ArrowLeft":
      case "p":
      case "N":
        jump("change", -1);
        break;
      case "x":
        jump("x", 1);
        break;
      case "Home":
        goEdge(-1);
        break;
      case "End":
        goEdge(1);
        break;
      case "X":
        jump("x", -1);
        break;
      case "ArrowDown":
        if (rows.length) setSelected(rows[Math.min(rows.length - 1, idx + 1)].key);
        break;
      case "ArrowUp":
        if (rows.length) setSelected(rows[Math.max(0, idx - 1)].key);
        break;
      case "m":
        setMarker(cursor);
        break;
      case "Escape":
        setMarker(null);
        setXPop(null);
        break;
      case "Delete":
      case "Backspace":
        if (selected && !selected.includes("#")) removeSig(selected);
        break;
      default:
        return;
    }
    e.preventDefault();
  };

  // ---------------------------------------------------------------- pointer: drag = pan, click = cursor
  const drag = useRef<{ x: number; moved: boolean; id: number } | null>(null);
  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.button !== 0) return;
    drag.current = { x: e.clientX, moved: false, id: e.pointerId };
    e.currentTarget.setPointerCapture(e.pointerId);
    gridRef.current?.focus({ preventScroll: true });
  };
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    if (!d.moved && Math.abs(dx) < 3) return;
    d.moved = true;
    d.x = e.clientX;
    rendRef.current?.pan(-dx);
  };
  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    drag.current = null;
    if (!d || d.moved) return;
    const r = rendRef.current;
    if (!r) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const t = Math.round(xToT(x, r.view));
    if (e.shiftKey) {
      setMarker(t);
      return;
    }
    setCursor(t);
    const ri = Math.floor((y + r.scrollTop) / ROW_H);
    const row = rows[ri];
    if (row) {
      setSelected(row.key);
      const d2 = r.rowData(row);
      const v = d2 ? valueAt(d2, t) : null;
      if (v && v !== "*" && (hasX(v) || hasZ(v)) && !row.field) setXPop({ x, y: ri * ROW_H - r.scrollTop + ROW_H, signal: row.path, t });
      else setXPop(null);
    }
  };
  // ctrl/meta+wheel zooms at the pointer; horizontal / shift+wheel pans; plain wheel scrolls rows
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const onWheel = (e: WheelEvent) => {
      const r = rendRef.current;
      if (!r) return;
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const rect = c.getBoundingClientRect();
        r.zoom(Math.exp(e.deltaY * 0.0025), e.clientX - rect.left);
      } else if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        e.preventDefault();
        r.pan(e.shiftKey ? e.deltaY : e.deltaX);
      }
    };
    c.addEventListener("wheel", onWheel, { passive: false });
    return () => c.removeEventListener("wheel", onWheel);
  }, []);

  // ---------------------------------------------------------------- render helpers
  const first = Math.max(0, Math.floor(scrollTop / ROW_H));
  const last = Math.min(rows.length, Math.ceil((scrollTop + gridH) / ROW_H) + 1);
  const visible = rows.slice(first, last);
  void dataVer; // re-render value column when data arrives

  const unit = pickUnit(Math.max(1, view.t1 - view.t0) / 10, ts);
  const fmtT = (t: number | null) => (t === null ? "—" : formatTime(t, ts, unit, 3));
  const sigOf = (key: string) => sigs.find((s) => s.path === key.split("#")[0]);
  const openSource = async (path: string) => {
    setStatus("locating source…");
    try {
      setSrc(await locateSignal(path));
      setStatus(null);
    } catch (e) {
      setStatus(`source not available: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const reorder = (from: number, to: number) => {
    setSigs((ss) => {
      const n = ss.slice();
      const [m] = n.splice(from, 1);
      n.splice(to > from ? to - 1 : to, 0, m);
      return n;
    });
  };

  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0 }}>
      <div className="waves" style={{ flex: "1 1 auto", minWidth: 0 }}>
        <Hierarchy run={runId} onAdd={addSignals} />
        <div className="waves-main">
          <div className="wtool">
            <span className="mono small ellipsis" title={meta.file} style={{ maxWidth: 220 }}>
              <a href={`#/runs/${runId}`}>{runId}</a> · {meta.file}
            </span>
            <span className="spacer" />
            <button className="icon" onClick={() => rendRef.current?.zoom(2)} title="Zoom out (-)" aria-label="Zoom out">
              −
            </button>
            <button className="icon" onClick={() => rendRef.current?.zoom(0.5)} title="Zoom in (+)" aria-label="Zoom in">
              +
            </button>
            <button onClick={() => rendRef.current?.fit()} title="Zoom to fit (f)">
              Fit
            </button>
            <button onClick={() => goEdge(-1)} title="Go to start (Home)">
              ⇤ start
            </button>
            <button onClick={() => jump("change", -1)} disabled={!selRow} title="Previous change of selected signal (←)">
              ◀ change
            </button>
            <button onClick={() => jump("change", 1)} disabled={!selRow} title="Next change of selected signal (→)">
              change ▶
            </button>
            <button onClick={() => goEdge(1)} title="Go to end (End)">
              end ⇥
            </button>
            <button onClick={() => jump("x", 1)} disabled={!selRow} title="Next X/Z of selected signal (x; shift+x for previous)">
              next X
            </button>
            <span className="readout" title="cursor time / marker time / distance between them">
              <span style={{ color: "var(--w-cursor)" }}>▼ {fmtT(cursor)}</span>
              {marker !== null && (
                <>
                  {"  "}
                  <span style={{ color: "var(--w-marker)" }}>▼ {fmtT(marker)}</span>
                  {cursor !== null && <> apart {formatTime(Math.abs(cursor - marker), ts, undefined, 3)}</>}
                </>
              )}
            </span>
          </div>
          {status && (
            <div className="small muted" style={{ padding: "2px 8px", borderBottom: "1px solid var(--border)" }} role="status">
              {status}{" "}
              <button className="ghost" style={{ height: 18 }} onClick={() => setStatus(null)} aria-label="dismiss">
                ✕
              </button>
            </div>
          )}
          <div className="wheader">
            <div>Signals ({sigs.length})</div>
            <div>Value @ cursor</div>
            <div className="waxis">
              <canvas ref={axisRef} />
            </div>
          </div>
          <div
            className="wgrid"
            ref={gridRef}
            tabIndex={0}
            onKeyDown={onKey}
            onScroll={(e) => {
              const st = e.currentTarget.scrollTop;
              setScrollTop(st);
              rendRef.current?.setScroll(st);
            }}
            aria-label="Waveform. Keys: + − zoom, f fit, ←/→ prev/next change, Home/End start/end, x next X, ↑/↓ select, m marker, Delete remove"
          >
            <div className="wgrid-inner" style={{ height: Math.max(rows.length * ROW_H, gridH) }}>
              <div className="wsticky" style={{ height: gridH }}>
                <div className="wnames">
                  {visible.map((r, k) => {
                    const i = first + k;
                    const sig = sigOf(r.key);
                    const topIdx = r.field ? -1 : sigs.findIndex((s) => s.path === r.path);
                    const leaf = r.field ? r.key.split("#")[1] : r.path;
                    return (
                      <div
                        key={r.key}
                        className={`wrow-name ${r.field ? "field" : ""} ${r.key === selected ? "sel" : ""} ${dropIdx === topIdx && dragIdx !== null && topIdx >= 0 ? "drop-above" : ""}`}
                        style={{ top: i * ROW_H - scrollTop, height: ROW_H }}
                        onClick={() => setSelected(r.key)}
                        onDoubleClick={() => openSource(r.path)}
                        title={`${r.key.replace("#", ".")}${r.width > 1 ? ` [${r.width - 1}:0]` : ""}\ndouble-click: show source · drag to reorder`}
                        draggable={!r.field}
                        onDragStart={(e) => {
                          setDragIdx(topIdx);
                          e.dataTransfer.effectAllowed = "move";
                        }}
                        onDragOver={(e) => {
                          if (dragIdx === null || topIdx < 0) return;
                          e.preventDefault();
                          setDropIdx(topIdx);
                        }}
                        onDrop={(e) => {
                          e.preventDefault();
                          if (dragIdx !== null && topIdx >= 0 && dragIdx !== topIdx) reorder(dragIdx, topIdx);
                          setDragIdx(null);
                          setDropIdx(null);
                        }}
                        onDragEnd={() => {
                          setDragIdx(null);
                          setDropIdx(null);
                        }}
                      >
                        {!r.field && sig?.decode?.kind === "struct" ? (
                          <span className="tw" onClick={(e) => (e.stopPropagation(), updSig(r.path, (s) => ({ ...s, expanded: !s.expanded })))}>
                            {sig.expanded ? "▾" : "▸"}
                          </span>
                        ) : (
                          <span className="tw" />
                        )}
                        {r.field ? (
                          <span className="nm-leaf">{leaf}</span>
                        ) : (
                          <>
                            <span className="nm-scope">{leaf.slice(0, leaf.length - (leaf.split(".").pop() ?? "").length)}</span>
                            <span className="nm-leaf">{leaf.split(".").pop()}</span>
                          </>
                        )}
                        {r.width > 1 && !r.real && (
                          <select
                            value={r.radix}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => {
                              const v = e.target.value as Radix;
                              if (r.field) return;
                              updSig(r.path, (s) => ({ ...s, radix: v }));
                            }}
                            disabled={!!r.field}
                            aria-label={`radix of ${r.key}`}
                          >
                            {RADIXES.filter((x) => x !== "enum" || r.enumMap).map((x) => (
                              <option key={x}>{x}</option>
                            ))}
                          </select>
                        )}
                        {!r.field && (
                          <button className="x" onClick={(e) => (e.stopPropagation(), removeSig(r.path))} aria-label={`remove ${r.path}`} title="Remove">
                            ✕
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
                <div className="wvals">
                  {visible.map((r, k) => {
                    const i = first + k;
                    const d = rendRef.current?.rowData(r);
                    const raw = d && cursor !== null ? valueAt(d, cursor) : null;
                    const txt = raw === null ? "" : raw === "*" ? "…" : formatValue(raw, r.width, r.radix, r.enumMap);
                    const isX = raw !== null && raw !== "*" && (hasX(raw) || hasZ(raw));
                    return (
                      <div key={r.key} className={`wrow-val ${r.key === selected ? "sel" : ""}`} style={{ top: i * ROW_H - scrollTop, height: ROW_H }} title={raw ?? ""}>
                        {isX && !r.field && cursor !== null ? (
                          <span className="xv ellipsis">
                            <a href={href("/xtrace", { run: runId, signal: r.path, time: Math.round(cursor) })} title="Trace this X">
                              {txt}
                            </a>
                          </span>
                        ) : (
                          <span className={`ellipsis ${isX ? "xv" : ""}`}>{txt}</span>
                        )}
                      </div>
                    );
                  })}
                </div>
                <div className="wcanvas-wrap" ref={wrapRef}>
                  <canvas ref={canvasRef} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={() => (drag.current = null)} />
                  {xPop && (
                    <div className="panel" style={{ position: "absolute", left: Math.min(xPop.x + 6, Math.max(0, (wrapRef.current?.clientWidth ?? 300) - 190)), top: xPop.y, zIndex: 3, padding: 4, boxShadow: "0 2px 8px rgba(0,0,0,.15)" }}>
                      <a className="btn" href={href("/xtrace", { run: runId, signal: xPop.signal, time: xPop.t })}>
                        Trace this X →
                      </a>
                    </div>
                  )}
                </div>
              </div>
            </div>
            {sigs.length === 0 && (
              <div style={{ position: "absolute", top: 40, left: 0, right: 0 }}>
                <Empty title="No signals yet">Pick signals from the hierarchy or search on the left.</Empty>
              </div>
            )}
          </div>
        </div>
      </div>
      {src && (
        <div className="side-panel">
          <SourcePanel file={src.file} line={src.line} onClose={() => setSrc(null)} />
        </div>
      )}
    </div>
  );
}
