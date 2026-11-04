import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { DesignHierarchy, RunSummary, Schematic, SchematicCell } from "../api/types";
import { Empty, ErrorState, Loading } from "../components/States";
import { plural } from "../lib/format";
import { cellKey, decorate, draw, sanitize, setZoom } from "../lib/schematic";
import { useAsync } from "../lib/hooks";
import { Info } from "../components/Info";
import { href, navigate } from "../lib/router";

const ZOOM_MIN = 0.1;
const ZOOM_MAX = 4;
const ZOOM_READABLE = 0.7;

/** Draws a schematic (Blocks or Gates: same code) in the background worker and shows it. */
function SchematicView({
  data,
  what,
  clickable,
  onOpen,
  allNames = true,
}: {
  data: Schematic;
  what: string;
  clickable?: (c: SchematicCell) => boolean;
  onOpen?: (key: string, c: SchematicCell) => void;
  allNames?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string | null>(null);
  const [err, setErr] = useState<Error | null>(null);
  const [zoom, setZ] = useState<number | null>(null); // null = fit on first draw
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    setSvg(null);
    setErr(null);
    setZ(null);
    const job = draw(data.netlist);
    job.promise.then((r) => setSvg(sanitize(r.svg)), (e: Error) => setErr(e));
    return job.cancel;
  }, [data]);

  useLayoutEffect(() => {
    const h = host.current;
    if (!h || svg == null) return;
    h.innerHTML = svg;
    const el = h.querySelector("svg");
    if (!el) return;
    const sz = decorate(el, data, { clickable });
    setSize({ w: sz.width, h: sz.height });
    // start at 100%, or smaller to show more of a wide drawing, but never below a readable size
    const fit = sz.width > 0 ? (h.clientWidth - 2) / sz.width : 1;
    setZ((z) => z ?? Math.min(1, Math.max(ZOOM_READABLE, fit)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [svg]);

  useLayoutEffect(() => {
    const el = host.current?.querySelector("svg");
    if (el && zoom != null) setZoom(el, zoom);
  }, [zoom, svg]);

  // Ctrl/⌘ + wheel (and trackpad pinch, which browsers send as ctrl+wheel) zooms the diagram around
  // the cursor instead of the page. Needs a non-passive listener to stop the browser's page zoom.
  const zoomRef = useRef(1);
  zoomRef.current = zoom ?? 1;
  useEffect(() => {
    const h = host.current;
    if (!h || svg == null) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const el = h.querySelector("svg");
      if (!el) return;
      const z = zoomRef.current;
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY;
      const nz = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * Math.exp(-dy * 0.002)));
      if (nz === z) return;
      const r = el.getBoundingClientRect();
      const px = e.clientX - r.left;
      const py = e.clientY - r.top;
      setZoom(el, nz); // resize now so the scroll correction below lands on the new size
      h.scrollLeft += px * (nz / z - 1);
      h.scrollTop += py * (nz / z - 1);
      zoomRef.current = nz;
      setZ(nz);
    };
    h.addEventListener("wheel", onWheel, { passive: false });
    return () => h.removeEventListener("wheel", onWheel);
  }, [svg]);

  const open = (target: EventTarget | null) => {
    const key = cellKey(target as Element | null);
    const c = key != null ? data.cells[key] : undefined;
    if (key != null && c && clickable?.(c)) onOpen?.(key, c);
  };
  const fit = () => {
    const w = host.current?.clientWidth ?? 0;
    if (size.w > 0 && w > 0) setZ(Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, (w - 2) / size.w)));
  };
  const n = Object.keys(data.cells).length;

  return (
    <>
      <div className="row wrap" style={{ justifyContent: "flex-end" }}>
        <ZoomBar zoom={zoom ?? 1} setZoom={setZ} onFit={fit} disabled={svg == null} />
      </div>
      {err ? (
        <ErrorState error={err} feature="Schematic drawing" />
      ) : (
        <>
          {svg == null && <Loading what={`Laying out ${plural(n, what)}`} />}
          <div
            ref={host}
            className={`diagram schematic ${allNames ? "" : "path-names"}`}
            hidden={svg == null}
            onClick={(e) => open(e.target)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                open(e.target);
              }
            }}
          />
        </>
      )}
    </>
  );
}

function ZoomBar({ zoom, setZoom, onFit, disabled }: { zoom: number; setZoom: (z: number) => void; onFit: () => void; disabled?: boolean }) {
  return (
    <span className="row" style={{ gap: 4 }}>
      <button className="icon" disabled={disabled} onClick={() => setZoom(Math.max(ZOOM_MIN, zoom / 1.25))} aria-label="Zoom out">
        −
      </button>
      <span className="small muted num" style={{ width: 40, textAlign: "center" }}>
        {Math.round(zoom * 100)}%
      </span>
      <button className="icon" disabled={disabled} onClick={() => setZoom(Math.min(ZOOM_MAX, zoom * 1.25))} aria-label="Zoom in">
        +
      </button>
      <button disabled={disabled} onClick={() => setZoom(1)}>
        100%
      </button>
      <button disabled={disabled} onClick={onFit}>
        Fit
      </button>
    </span>
  );
}

// ------------------------------------------------------------------ block diagram
function BlockDiagram({ module, trail }: { module: string; trail: string[] }) {
  const hier = useAsync(() => api.hierarchy(), []);
  const top = hier.data?.top;
  const mod = module || top || "";
  const block = useAsync(() => (mod ? api.schematicBlock(mod) : Promise.resolve<Schematic | null>(null)), [mod]);
  const g = block.data;
  const h = hier.data as DesignHierarchy | undefined;
  const canOpen = (c: SchematicCell) => c.kind === "instance" && !!c.module && (!h || !!h.modules[c.module]);
  const descend = (_key: string, c: SchematicCell) => {
    navigate(href("/design", { module: c.module!, trail: [...trail, `${c.name}:${c.module}`].join(",") }));
  };
  if (hier.error && !module) return <ErrorState error={hier.error} feature="Design hierarchy" onRetry={hier.reload} />;
  if (!mod) return hier.loading ? <Loading what="Loading hierarchy" /> : <Empty title="No design loaded" cmd="svkit init" />;
  const instances = g?.counts.instances ?? 0;
  const hasLogic = !!g && Object.values(g.cells).some((c) => c.kind === "logic");
  return (
    <>
      <div className="row wrap">
        <div className="crumbs" aria-label="Hierarchy">
          <a href={href("/design", { module: top ?? mod })}>{top ?? mod}</a>
          {trail.map((t, i) => {
            const [inst, m] = t.split(":");
            return (
              <span key={i} className="row" style={{ gap: 4 }}>
                <span className="faint">/</span>
                <a href={href("/design", { module: m, trail: trail.slice(0, i + 1).join(",") })} title={m}>
                  {inst}
                </a>
              </span>
            );
          })}
          <span className="faint">({mod})</span>
        </div>
        <span className="spacer" />
        {g && (
          <span className="small muted">
            {plural(instances, "instance")} · {plural(g.counts.ports ?? 0, "port")} · {plural(g.counts.nets ?? 0, "net")}
          </span>
        )}
      </div>
      {block.error ? (
        <ErrorState error={block.error} feature="Block diagrams" onRetry={block.reload} />
      ) : !g ? (
        <Loading what="Loading block diagram" />
      ) : instances === 0 && !hasLogic ? (
        <Empty title={`${mod} has no child instances`}>It is a leaf module.</Empty>
      ) : (
        <SchematicView data={g} what="block" clickable={canOpen} onOpen={descend} />
      )}
      <div className="small muted">
        Click an instance (or focus it and press Enter) to descend into it. Hover anything for details. Ctrl + scroll (or pinch) zooms.
      </div>
    </>
  );
}

// ------------------------------------------------------------------ gate cone
const isSynth = (r: RunSummary) => (r.kind === "synth" || r.kind === "sweep-point") && r.status === "done";

/** Gates tab opened directly: show the newest finished synthesis run's worst timing path. */
function GatesStart() {
  const runs = useAsync(() => api.runs({ kind: "synth", limit: 200 }), []);
  const latest = runs.data?.find(isSynth);
  useEffect(() => {
    if (latest) navigate(href("/design", { cone: latest.id, path: 0 }), true);
  }, [latest]);
  if (runs.error) return <ErrorState error={runs.error} feature="Gate cone view" onRetry={runs.reload} />;
  if (runs.loading || latest) return <Loading what="Finding the latest synthesis run" />;
  return (
    <Empty title="No synthesis run yet" cmd="svkit synth">
      The gate view draws the synthesized netlist around a timing path. Run synthesis (here or from the Synthesis view), then
      come back.
    </Empty>
  );
}

/** Switch the cone to another finished synthesis run (its worst path). */
function RunPicker({ run }: { run: string }) {
  const runs = useAsync(() => api.runs({ kind: "synth", limit: 200 }), []);
  const list = (runs.data ?? []).filter(isSynth);
  if (!list.some((r) => r.id === run)) list.unshift({ id: run } as RunSummary);
  return (
    <select
      className="mono"
      aria-label="Synthesis run"
      style={{ maxWidth: 280 }}
      value={run}
      onChange={(e) => navigate(href("/design", { cone: e.target.value, path: 0 }))}
    >
      {list.map((r) => (
        <option key={r.id} value={r.id}>
          {r.id}
          {r.experiment ? ` · ${r.experiment}` : ""}
          {r.knobs && Object.keys(r.knobs).length ? ` · ${Object.entries(r.knobs).map(([k, v]) => `${k}=${v}`).join(" ")}` : ""}
        </option>
      ))}
    </select>
  );
}

function ConeDiagram({ run, path, net, depth }: { run: string; path: string; net: string; depth: number }) {
  const [netIn, setNetIn] = useState(net);
  const [depthIn, setDepthIn] = useState(depth);
  const [allNames, setAllNames] = useState(false);
  const cone = useAsync(
    () => api.schematicCone({ run, net: net || undefined, depth, path: path === "" ? undefined : path }),
    [run, net, depth, path],
  );
  const g = cone.data;
  const cells = g?.counts.cells ?? 0;
  const onPath = g?.counts.on_path ?? 0;
  const unresolved = g?.unresolved ?? [];
  return (
    <>
      <form
        className="row wrap"
        onSubmit={(e) => {
          e.preventDefault();
          navigate(href("/design", { cone: run, net: netIn, depth: depthIn, path: netIn ? "" : path }));
        }}
      >
        <span className="small">
          Gate cone · run
        </span>
        <RunPicker run={run} />
        <span className="small">
          <a href={`#/synth/${run}`}>timing paths</a>
          {path !== "" && <> · path #{path}{path === "0" ? " (worst)" : ""}</>}
        </span>
        <label className="field">
          net
          <input className="mono" value={netIn} onChange={(e) => setNetIn(e.target.value)} placeholder="u_core/result_d[7]" style={{ width: 220 }} />
        </label>
        <label className="field">
          depth
          <input type="number" min={1} max={50} value={depthIn} onChange={(e) => setDepthIn(Number(e.target.value))} style={{ width: 56 }} />
        </label>
        <button type="submit">Show</button>
        <label className="field">
          <input type="checkbox" checked={allNames} onChange={(e) => setAllNames(e.target.checked)} />
          all instance names
        </label>
        <span className="spacer" />
        {g && (
          <span className="small muted">
            {plural(cells, "cell")} · {onPath} on path
            {cells > 0 && g.counts.symbols < cells && <> · {plural(cells - g.counts.symbols, "box", "boxes")}</>}
          </span>
        )}
      </form>
      {unresolved.length > 0 && (
        <div className="small worse anywhere">
          {plural(unresolved.length, "path point")} could not be matched to gates in the synthesized netlist:{" "}
          <span className="mono">
            {unresolved
              .slice(0, 10)
              .map((u) => (u.inst ? `${u.inst}/${u.pin}` : u.pin))
              .join(", ")}
            {unresolved.length > 10 ? " …" : ""}
          </span>
        </div>
      )}
      {cone.error ? (
        <ErrorState error={cone.error} feature="Gate cone view" onRetry={cone.reload} />
      ) : !g ? (
        <Loading what="Loading gate cone" />
      ) : cells === 0 ? (
        <Empty title="No gates found">Nothing in the synthesized design drives this signal.</Empty>
      ) : (
        <SchematicView data={g} what="cell" allNames={allNames} />
      )}
      <div className="small muted">
        Cells on the critical path are drawn in red with their cell delay. Library cells without a gate symbol are boxes
        with their pin names. Hover a cell for its type. Ctrl + scroll (or pinch) zooms.
      </div>
    </>
  );
}

export default function DesignView({ query }: { query: URLSearchParams }) {
  const cone = query.get("cone");
  const gates = !!cone || query.get("gates") === "1";
  const trail = (query.get("trail") ?? "").split(",").filter(Boolean);
  return (
    <div className="view">
      <div className="view-head">
        <h1>Design</h1>
        <div className="seg">
          <button className={!gates ? "on" : ""} onClick={() => navigate("/design")}>
            Blocks
          </button>
          <button className={gates ? "on" : ""} onClick={() => !cone && navigate(href("/design", { gates: 1 }))}>
            Gates
          </button>
        </div>
        <Info term="gate_cone" />
      </div>
      {gates && !cone ? (
        <GatesStart />
      ) : cone ? (
        <ConeDiagram run={cone} path={query.get("path") ?? ""} net={query.get("net") ?? ""} depth={Number(query.get("depth") ?? 6) || 6} />
      ) : (
        <BlockDiagram module={query.get("module") ?? ""} trail={trail} />
      )}
    </div>
  );
}
