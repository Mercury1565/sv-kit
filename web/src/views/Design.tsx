import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BlockGraph, ConeGraph, DesignHierarchy } from "../api/types";
import { Empty, ErrorState, Loading } from "../components/States";
import { fmtNum, plural } from "../lib/format";
import { blockToElk, coneToElk, getElk, toDrawing, type Drawing } from "../lib/graph";
import { useAsync } from "../lib/hooks";
import { href, navigate } from "../lib/router";

function useLayout(build: (() => Promise<Drawing>) | null, key: string) {
  const [d, setD] = useState<Drawing | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    let live = true;
    setD(null);
    setErr(null);
    if (!build) return;
    build().then(
      (x) => live && setD(x),
      (e) => live && setErr(e),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return { drawing: d, error: err };
}

function DiagramSvg({ d, onNode, zoom }: { d: Drawing; onNode?: (id: string, module?: string | null) => void; zoom: number }) {
  return (
    <svg width={d.width * zoom} height={d.height * zoom} viewBox={`-10 -10 ${d.width} ${d.height}`} role="img" aria-label={`diagram with ${d.nodes.length} nodes`}>
      <defs>
        <marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill="currentColor" style={{ color: "var(--fg-faint)" }} />
        </marker>
      </defs>
      {d.edges.map((e) =>
        e.points.length ? (
          <g key={e.id} className={`edge ${e.onPath ? "on" : ""} ${e.width && e.width > 1 ? "bus" : ""}`}>
            <path d={"M" + e.points.map((p) => `${p.x},${p.y}`).join(" L")} markerEnd="url(#arr)" />
            {e.label && (
              <text x={e.points[0].x + 6} y={e.points[0].y - 4}>
                {e.label}
              </text>
            )}
          </g>
        ) : null,
      )}
      {d.nodes.map((n) => {
        const click = !!onNode && !n.io && !!n.module;
        return (
          <g key={n.id} className={`node ${click ? "click" : ""} ${n.onPath ? "on" : ""}`} onClick={click ? () => onNode!(n.id, n.module) : undefined}>
            <title>{[n.label, n.sub].filter(Boolean).join("\n")}</title>
            <rect x={n.x} y={n.y} width={n.w} height={n.h} rx={n.io ? 11 : 3} />
            {n.ports.length > 0 ? (
              // ported blocks: names outside the box so they never collide with port labels
              <>
                <text x={n.x} y={n.y - 5}>
                  {n.label}
                </text>
                {n.sub && (
                  <text className="sub" x={n.x} y={n.y + n.h + 12}>
                    {n.sub}
                  </text>
                )}
              </>
            ) : (
              <>
                <text x={n.x + n.w / 2} y={n.y + 15} textAnchor="middle">
                  {n.label}
                </text>
                {n.sub && (
                  <text className="sub" x={n.x + n.w / 2} y={n.y + 29} textAnchor="middle">
                    {n.sub}
                  </text>
                )}
              </>
            )}
            {n.delay != null && (
              <text className="delay" x={n.x + n.w / 2} y={n.y + n.h - 7} textAnchor="middle" fontSize={11}>
                {fmtNum(n.delay, 1)} ps
              </text>
            )}
            {n.ports.map((p) => (
              <g key={p.id} className="port">
                <rect x={p.x} y={p.y} width={6} height={6} />
                <text x={p.side === "W" ? p.x + 9 : p.x - 3} y={p.y + 6} textAnchor={p.side === "W" ? "start" : "end"}>
                  {p.name}
                </text>
              </g>
            ))}
          </g>
        );
      })}
    </svg>
  );
}

function ZoomBar({ zoom, setZoom }: { zoom: number; setZoom: (z: number) => void }) {
  return (
    <span className="row" style={{ gap: 4 }}>
      <button className="icon" onClick={() => setZoom(Math.max(0.2, zoom / 1.25))} aria-label="Zoom out">
        −
      </button>
      <span className="small muted num" style={{ width: 40 }}>
        {Math.round(zoom * 100)}%
      </span>
      <button className="icon" onClick={() => setZoom(Math.min(4, zoom * 1.25))} aria-label="Zoom in">
        +
      </button>
      <button onClick={() => setZoom(1)}>100%</button>
    </span>
  );
}

// ------------------------------------------------------------------ block diagram
function BlockDiagram({ module, trail }: { module: string; trail: string[] }) {
  const hier = useAsync(() => api.hierarchy(), []);
  const top = hier.data?.top;
  const mod = module || top || "";
  const block = useAsync(() => (mod ? api.block(mod) : Promise.resolve<BlockGraph | null>(null)), [mod]);
  const [zoom, setZoom] = useState(1);
  const g = block.data;
  const layout = useLayout(
    g
      ? async () => {
          const elk = await getElk();
          const laid = await elk.layout(blockToElk(g));
          const byId = new Map(g.nodes.map((n) => [n.id, n]));
          return toDrawing(
            laid,
            (id) => {
              const n = byId.get(id);
              return { label: n?.label ?? id, sub: n?.module ?? undefined, module: n?.module };
            },
            (i) => ({ width: g.edges[i]?.width, label: g.edges[i]?.width && g.edges[i].width! > 1 ? `[${g.edges[i].width! - 1}:0]` : undefined }),
          );
        }
      : null,
    `${mod}:${g ? g.nodes.length : -1}`,
  );
  const descend = (inst: string, child?: string | null) => {
    if (!child) return;
    const h = hier.data as DesignHierarchy | undefined;
    if (h && !h.modules[child]) return;
    navigate(href("/design", { module: child, trail: [...trail, `${inst}:${child}`].join(",") }));
  };
  if (hier.error && !module) return <ErrorState error={hier.error} feature="Design hierarchy" onRetry={hier.reload} />;
  if (!mod) return hier.loading ? <Loading what="Loading hierarchy" /> : <Empty title="No design loaded" cmd="svkit init" />;
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
        {g && <span className="small muted">{plural(g.nodes.length, "instance")} · {plural(g.edges.length, "connection")}</span>}
        <ZoomBar zoom={zoom} setZoom={setZoom} />
      </div>
      {block.error ? (
        <ErrorState error={block.error} feature="Block diagrams" onRetry={block.reload} />
      ) : layout.error ? (
        <ErrorState error={layout.error} />
      ) : g && g.nodes.length === 0 ? (
        <Empty title={`${mod} has no child instances`}>It is a leaf module.</Empty>
      ) : !layout.drawing ? (
        <Loading what="Laying out" />
      ) : (
        <div className="diagram">
          <DiagramSvg d={layout.drawing} zoom={zoom} onNode={(id, m) => descend(id, m)} />
        </div>
      )}
      <div className="small muted">Click an instance to descend into it.</div>
    </>
  );
}

// ------------------------------------------------------------------ gate cone
function ConeDiagram({ run, path, net, depth }: { run: string; path: string; net: string; depth: number }) {
  const [netIn, setNetIn] = useState(net);
  const [depthIn, setDepthIn] = useState(depth);
  const [zoom, setZoom] = useState(1);
  const cone = useAsync(
    () => api.cone({ run, net: net || undefined, depth, path: path === "" ? undefined : path }),
    [run, net, depth, path],
  );
  const g = cone.data as ConeGraph | undefined;
  const layout = useLayout(
    g
      ? async () => {
          const elk = await getElk();
          const laid = await elk.layout(coneToElk(g));
          const byId = new Map(g.nodes.map((n) => [n.id, n]));
          return toDrawing(
            laid,
            (id) => {
              const n = byId.get(id);
              return { label: id, sub: n?.cell ?? undefined, onPath: !!n?.on_path, delay: n?.delay ?? null };
            },
            (i) => ({ onPath: !!g.edges[i]?.on_path }),
          );
        }
      : null,
    `${run}:${net}:${depth}:${path}:${g ? g.nodes.length : -1}`,
  );
  const onPathCount = g?.nodes.filter((n) => n.on_path).length ?? 0;
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
          Gate cone · run <a className="mono" href={`#/synth/${run}`}>{run}</a>
          {path !== "" && <> · critical path #{path}</>}
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
        <span className="spacer" />
        {g && (
          <span className="small muted">
            {plural(g.nodes.length, "cell")} · {onPathCount} on path
          </span>
        )}
        <ZoomBar zoom={zoom} setZoom={setZoom} />
      </form>
      {g && g.unresolved.length > 0 && (
        <div className="small worse anywhere">
          {plural(g.unresolved.length, "path point")} could not be mapped to netlist instances: <span className="mono">{g.unresolved.slice(0, 10).join(", ")}{g.unresolved.length > 10 ? " …" : ""}</span>
        </div>
      )}
      {cone.error ? (
        <ErrorState error={cone.error} feature="Gate cone view" onRetry={cone.reload} />
      ) : layout.error ? (
        <ErrorState error={layout.error} />
      ) : g && g.nodes.length === 0 ? (
        <Empty title="Empty cone">No driving cells found for this net.</Empty>
      ) : !layout.drawing ? (
        <Loading what="Laying out" />
      ) : (
        <div className="diagram">
          <DiagramSvg d={layout.drawing} zoom={zoom} />
        </div>
      )}
      <div className="small muted">Cells on the critical path are outlined in red with their cell delay.</div>
    </>
  );
}

export default function DesignView({ query }: { query: URLSearchParams }) {
  const cone = query.get("cone");
  const trail = (query.get("trail") ?? "").split(",").filter(Boolean);
  return (
    <div className="view">
      <div className="view-head">
        <h1>Design</h1>
        <div className="seg">
          <button className={!cone ? "on" : ""} onClick={() => navigate("/design")}>
            Block diagram
          </button>
          <button className={cone ? "on" : ""} disabled={!cone} title={cone ? undefined : "Open a gate cone from a timing path in a synthesis run"}>
            Gate cone
          </button>
        </div>
      </div>
      {cone ? (
        <ConeDiagram run={cone} path={query.get("path") ?? ""} net={query.get("net") ?? ""} depth={Number(query.get("depth") ?? 6) || 6} />
      ) : (
        <BlockDiagram module={query.get("module") ?? ""} trail={trail} />
      )}
    </div>
  );
}
