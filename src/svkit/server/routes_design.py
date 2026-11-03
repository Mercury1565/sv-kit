"""Design + X-trace API (docs/ARCHITECTURE.md §6): hierarchy, block diagram, gate cone, xtrace."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from svkit.schemas import TimingPath

NETLIST_EXTS = (".vg", ".v", ".sv", ".json")


def find_netlist(db, run) -> Path:
    """The gate netlist of a synth run: ``metrics.netlist`` (the one STA read) or any netlist artifact.
    Verilog is preferred over Yosys JSON because timing reports name cells as in the Verilog
    netlist (Yosys JSON keeps internal ``$abc$`` names)."""
    m = run.metrics or {}
    names = []
    if m.get("netlist"):
        names.append(m["netlist"])
    arts = list(run.artifacts)
    for ext in NETLIST_EXTS:
        names += [n for n in arts if n.lower().endswith(ext) and "netlist" in n.lower()]
    for ext in NETLIST_EXTS:
        names += [n for n in arts if n.lower().endswith(ext)]
    for n in dict.fromkeys(names):
        rel = run.artifacts.get(n)
        if rel is None:
            continue
        f = db.runs_dir / run.id / rel
        if f.is_file():
            return f
    raise HTTPException(404, f"run {run.id} has no netlist artifact")


def make_router(st) -> APIRouter:
    r = APIRouter(prefix="/api")
    lock = threading.Lock()
    nl_cache: dict[tuple, Any] = {}

    def graph():
        from svkit.design import graph as G
        try:
            return G.build(st.cfg)
        except Exception as e:
            raise HTTPException(422, f"could not build the design graph: {type(e).__name__}: {e}")

    def run_or_404(rid: str):
        try:
            return st.db.get_run(rid)
        except KeyError:
            raise HTTPException(404, f"no run {rid}")

    def load_netlist(f: Path, top: str):
        from svkit.design import netlist as N
        key = (str(f), f.stat().st_mtime_ns)
        with lock:
            if key in nl_cache:
                return nl_cache[key]
        libs = [st.cfg.root / p for p in st.cfg.library.liberty]
        try:
            nl = N.load(f, top=top, liberty=[p for p in libs if p.is_file()])
        except Exception as e:
            raise HTTPException(422, f"could not read netlist {f.name}: {e}")
        with lock:
            if len(nl_cache) >= 4:
                nl_cache.pop(next(iter(nl_cache)))
            nl_cache[key] = nl
        return nl

    @r.get("/design/hierarchy")
    def hierarchy() -> dict[str, Any]:
        return graph().hierarchy()

    @r.get("/design/signal")
    def signal(path: str) -> dict[str, Any]:
        from svkit.design import graph as G
        loc = G.graph_signal_location(graph(), path)
        if loc is None:
            raise HTTPException(404, f"signal {path!r} is not in the design")
        return loc

    @r.get("/design/block")
    def block(module: Optional[str] = None) -> dict[str, Any]:
        g = graph()
        try:
            return g.block(module)
        except KeyError:
            raise HTTPException(404, f"no module {module!r} in the design")

    @r.get("/design/cone")
    def cone(run: str, net: Optional[str] = None, depth: int = 4, path: Optional[int] = None) -> dict[str, Any]:
        from svkit.analysis import cone as C
        rr = run_or_404(run)
        m = rr.metrics or {}
        tp = None
        if path is not None:
            paths = m.get("paths") or []
            if not 0 <= path < len(paths):
                raise HTTPException(404, f"run {run} has no timing path #{path}")
            tp = TimingPath.model_validate(paths[path])
        if net is None and tp is None:
            raise HTTPException(400, "give `net` or `path`")
        nl = load_netlist(find_netlist(st.db, rr), m.get("top") or st.cfg.project.top)
        try:
            out = C.cone(nl, net, max(0, min(depth, 64)), path=tp)
        except (KeyError, ValueError) as e:
            raise HTTPException(404, str(e).strip("'\""))
        out["run"] = run
        out["netlist"] = Path(nl.source).name
        return out

    @r.get("/xtrace")
    def xtrace(signal: str, time: int, run: Optional[str] = None, depth: int = 12) -> dict[str, Any]:
        from svkit.analysis import xtrace as X
        from svkit.design.wavesrc import open_waves
        try:
            _, f = X.find_wave_run(st.db, run)
        except KeyError:
            raise HTTPException(404, f"no run {run}")
        except LookupError as e:
            raise HTTPException(404, str(e))
        node = X.trace(graph(), open_waves(f), signal, time, max(1, min(depth, 64)))
        return node.model_dump(mode="json")

    return r
