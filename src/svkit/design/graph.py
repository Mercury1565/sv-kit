"""DesignGraph: hierarchy, types and signal drivers of a SystemVerilog design, via pyslang.

``build(cfg)`` compiles ``[project].sources`` plus ``[project].tb_sources`` (so simulation
hierarchy paths such as ``tb.dut.x`` exist), elaborates, and extracts everything into plain
picklable data. The result is cached in memory and on disk (``.svkit/cache/``) by source hash.

Signal paths are dot-separated elaborated hierarchical names (``tb.dut.u_alu.res``), the same
shape simulators write into waveforms. Waveform names may carry a different root (``TOP.``,
no testbench, ...); :class:`PathAligner` maps between the two.

Nothing here knows about a particular project: tops, sources, include dirs, defines and the reset
name all come from ``svkit.toml``.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import re
import shlex
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from svkit.config import Config
from svkit.core import project as P
from svkit.design import expr as X

GRAPH_VERSION = 3


# ------------------------------------------------------------------ data model
@dataclass
class SignalInfo:
    path: str
    width: int
    kind: str                         # "var" | "net"
    type: str                         # type as printed by slang, e.g. "logic[7:0]", "pkg::state_t"
    file: Optional[str] = None
    line: Optional[int] = None
    mem: Optional[tuple[int, int]] = None   # (lo, hi) of the first unpacked dimension -> a memory
    elem_width: Optional[int] = None        # word width for memories
    port: Optional[str] = None              # "in" | "out" | "inout" when this is a port's internal signal
    scope: str = ""                          # containing instance path


@dataclass
class Assign:
    """One assignment to a signal inside a driver."""
    lhs: str
    rhs: tuple                         # expr.Expr
    guards: tuple = ()                 # ((cond_expr, polarity), ...) enclosing if/case conditions
    index: Optional[tuple] = None      # memory word index expr (writes to mem[idx])
    partial: bool = False              # writes only part of the signal (bit/range/member select)
    line: Optional[int] = None
    text: str = ""
    in_reset: bool = False             # inside the reset branch of an always_ff


@dataclass
class Driver:
    kind: str                          # assign | always_comb | always_ff | always_latch | port | initial | unknown
    file: Optional[str]
    line: Optional[int]
    text: str
    scope: str                         # instance path the driver lives in
    assigns: list[Assign] = field(default_factory=list)
    clock: Optional[str] = None        # always_ff: clock signal path
    edge: Optional[str] = None         # "posedge" | "negedge" | "edge"
    reset: Optional[str] = None        # always_ff: reset signal path (if a reset branch was recognised)
    reset_active: Optional[str] = None  # "high" | "low"
    has_reset_branch: bool = False
    data: list[str] = field(default_factory=list)    # RHS operands
    ctrl: list[str] = field(default_factory=list)    # if/case conditions, ternary selects
    index: list[str] = field(default_factory=list)   # array index expressions

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "assigns"}
        d["assigns"] = [{"lhs": a.lhs, "line": a.line, "text": a.text, "partial": a.partial,
                         "in_reset": a.in_reset, "mem_write": a.index is not None} for a in self.assigns]
        return d


@dataclass
class DesignGraph:
    top_module: str
    roots: list[str]                               # elaboration root instance paths
    design_tops: list[str]                         # instance paths of [project].top
    instances: dict[str, dict]                     # path -> {module, parent, file, line, conns:[...]}
    modules: dict[str, dict]                       # name -> {file, line, ports, instances}
    signals: dict[str, SignalInfo]
    drivers: dict[str, list[Driver]]
    types: dict[str, dict]                         # path -> decode info (non-plain only)
    diagnostics: list[str] = field(default_factory=list)
    source_hash: str = ""
    version: int = GRAPH_VERSION

    # -------------------------------------------------------------- queries
    def in_design(self, path: str) -> bool:
        """True if path lies inside an instance of the design top (vs. testbench)."""
        if not self.design_tops:
            return True
        return any(path == t or path.startswith(t + ".") for t in self.design_tops)

    def top_input(self, path: str) -> bool:
        s = self.signals.get(path)
        if not s or s.port not in ("in", "inout"):
            return False
        return s.scope in self.design_tops or (not self.design_tops and s.scope in self.roots)

    def scope_of(self, path: str) -> str:
        s = self.signals.get(path)
        return s.scope if s else path.rsplit(".", 1)[0]

    def hierarchy(self) -> dict[str, Any]:
        def tree(p: str) -> dict:
            inst = self.instances[p]
            kids = [c for c in self.children(p)]
            return {"name": p.rsplit(".", 1)[-1], "path": p, "module": inst["module"],
                    "children": [tree(c) for c in kids]}
        tops = self.design_tops or self.roots
        return {"top": self.top_module, "modules": self.modules,
                "tree": [tree(t) for t in tops if t in self.instances],
                "sim_roots": self.roots, "design_tops": self.design_tops,
                "diagnostics": self.diagnostics[:50]}

    def children(self, inst_path: str) -> list[str]:
        idx = self.__dict__.get("_kids")
        if idx is None:
            idx = {}
            for p, i in self.instances.items():
                if i.get("parent"):
                    idx.setdefault(i["parent"], []).append(p)
            self.__dict__["_kids"] = idx
        return idx.get(inst_path, [])

    def block(self, module: Optional[str] = None) -> dict[str, Any]:
        """ELK-ready block diagram of one module level: its ports and child instances as nodes,
        nets between them as edges. Glue logic (assign/always in the module) is one 'logic' node."""
        module = module or self.top_module
        if module not in self.modules:
            raise KeyError(module)
        cands = [p for p, i in self.instances.items() if i["module"] == module]
        if not cands:
            raise KeyError(module)
        cands.sort(key=lambda p: (not self.in_design(p), p.count("."), p))
        ip = cands[0]
        mod = self.modules[module]
        nodes: list[dict] = []
        srcs: dict[str, set] = {}
        sinks: dict[str, set] = {}
        for pt in mod["ports"]:
            nid = f"port:{pt['name']}"
            nodes.append({"id": nid, "label": pt["name"], "module": None, "kind": "port", "dir": pt["dir"],
                          "ports": [{"name": pt["name"], "dir": pt["dir"], "width": pt["width"]}]})
            sig = f"{ip}.{pt['name']}"
            if pt["dir"] in ("in", "inout"):
                srcs.setdefault(sig, set()).add((nid, pt["name"]))
            if pt["dir"] in ("out", "inout"):
                sinks.setdefault(sig, set()).add((nid, pt["name"]))
        for c in self.children(ip):
            ci = self.instances[c]
            name = c[len(ip) + 1:]
            cm = self.modules.get(ci["module"], {})
            nodes.append({"id": name, "label": name, "module": ci["module"], "kind": "instance",
                          "ports": [{"name": p["name"], "dir": p["dir"], "width": p["width"]}
                                    for p in cm.get("ports", [])]})
            for conn in ci.get("conns", []):
                for sig in conn["signals"]:
                    if conn["dir"] in ("out", "inout"):
                        srcs.setdefault(sig, set()).add((name, conn["port"]))
                    if conn["dir"] in ("in", "inout"):
                        sinks.setdefault(sig, set()).add((name, conn["port"]))
        logic = False
        for sig, drvs in self.drivers.items():
            for d in drvs:
                if d.scope != ip or d.kind == "port":
                    continue
                logic = True
                srcs.setdefault(sig, set()).add(("logic", None))
                for dep in {*d.data, *d.ctrl, *d.index}:
                    sinks.setdefault(dep, set()).add(("logic", None))
        if logic:
            nodes.append({"id": "logic", "label": "glue logic", "module": None, "kind": "logic", "ports": []})
        edges = []
        for sig in sorted(set(srcs) & set(sinks)):
            si = self.signals.get(sig)
            w = si.width if si else None
            for s, sp in sorted(srcs[sig], key=str):
                for d, dp in sorted(sinks[sig], key=str):
                    if s == d:
                        continue
                    edges.append({"id": f"e{len(edges)}", "src": s, "dst": d, "src_port": sp, "dst_port": dp,
                                  "width": w, "net": sig[len(ip) + 1:] if sig.startswith(ip + ".") else sig})
        return {"module": module, "instance": ip, "nodes": nodes, "edges": edges}


# ------------------------------------------------------------------ path alignment
class PathAligner:
    """Map hierarchical names between two namespaces whose roots differ (graph <-> waveform).

    Lookup is exact first, then through learned prefix pairs, then by the longest common
    component suffix (indexed by leaf name). Learned pairs make later lookups O(1)."""

    def __init__(self, names: Iterable[str]):
        self.names = set(names)
        self._by_leaf: Optional[dict[str, list[str]]] = None
        self.pairs: list[tuple[str, str]] = []   # (src_prefix, dst_prefix)
        self._memo: dict[str, Optional[str]] = {}

    def _leaf_index(self) -> dict[str, list[str]]:
        if self._by_leaf is None:
            idx: dict[str, list[str]] = {}
            for n in self.names:
                idx.setdefault(_leaf(n), []).append(n)
            self._by_leaf = idx
        return self._by_leaf

    def map(self, path: str) -> Optional[str]:
        if path in self.names:
            return path
        if path in self._memo:
            return self._memo[path]
        r = self._map(path)
        self._memo[path] = r
        return r

    def _map(self, path: str) -> Optional[str]:
        for sp, dp in self.pairs:
            if path.startswith(sp):
                cand = dp + path[len(sp):]
                if cand in self.names:
                    return cand
        parts = split_path(path)
        cands = self._leaf_index().get(parts[-1], [])
        best, best_n = None, 0
        for c in cands:
            cp = split_path(c)
            n = 0
            while n < min(len(cp), len(parts)) and cp[-1 - n] == parts[-1 - n]:
                n += 1
            if n > best_n or (n == best_n and best is not None and len(cp) < len(split_path(best))):
                best, best_n = c, n
        if best is None:
            return None
        if best_n == 1 and len(cands) > 1 and len(parts) > 1:
            return None          # leaf-only match is ambiguous
        sp = parts[:len(parts) - best_n]
        dp = split_path(best)[:len(split_path(best)) - best_n]
        pair = (".".join(sp) + "." if sp else "", ".".join(dp) + "." if dp else "")
        if best_n >= 2 and pair not in self.pairs and pair[0]:
            self.pairs.append(pair)
            self.pairs.sort(key=lambda x: -len(x[0]))
        return best


_SPLIT = re.compile(r"\\\S+\s?|[^.\\]+")


def split_path(p: str) -> list[str]:
    """Split a dotted hierarchical name, keeping escaped identifiers (``\\a.b ``) and
    brackets (``g[0]``) intact."""
    if "\\" not in p and "[" not in p:
        return p.split(".")
    out, cur, depth = [], "", 0
    i = 0
    while i < len(p):
        ch = p[i]
        if ch == "\\":
            j = p.find(" ", i)
            j = len(p) if j < 0 else j + 1
            cur += p[i:j]
            i = j
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "." and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur)
    return out


def _leaf(p: str) -> str:
    return split_path(p)[-1]


# ------------------------------------------------------------------ build / cache
_MEM_CACHE: dict[tuple[str, str], DesignGraph] = {}
_LOCK = threading.Lock()


def design_files(cfg: Config, with_tb: bool = True) -> list[Path]:
    files = P.resolve_sources(cfg)
    if with_tb:
        for f in P.resolve_sources(cfg, cfg.project.tb_sources):
            if f not in files:
                files.append(f)
    return files


def graph_hash(cfg: Config, with_tb: bool = True) -> str:
    h = hashlib.sha256()
    h.update(f"v{GRAPH_VERSION}|{cfg.project.top}|{cfg.project.tb_top}|{with_tb}|{sorted(cfg.project.defines.items())}"
             f"|{cfg.project.reset.name}".encode())
    for f in design_files(cfg, with_tb):
        h.update(str(f).encode())
        try:
            h.update(f.read_bytes())
        except OSError:
            pass
    for d in P.include_dirs(cfg):
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.is_file() and p.suffix in (".svh", ".vh", ".sv", ".v", ".h"):
                    h.update(p.read_bytes())
    return h.hexdigest()[:20]


def build(cfg: Config, *, with_tb: bool = True, use_cache: bool = True) -> DesignGraph:
    """Compile + elaborate the project and extract the DesignGraph (cached by source hash)."""
    hsh = graph_hash(cfg, with_tb)
    key = (str(cfg.root), hsh)
    with _LOCK:
        if use_cache and key in _MEM_CACHE:
            return _MEM_CACHE[key]
        cache_f = Path(cfg.root) / ".svkit" / "cache" / f"designgraph-{hsh}.pkl"
        if use_cache and cache_f.is_file():
            try:
                g = pickle.loads(cache_f.read_bytes())
                if isinstance(g, DesignGraph) and g.version == GRAPH_VERSION:
                    _MEM_CACHE[key] = g
                    return g
            except Exception:
                pass
        g = _Extractor(cfg, design_files(cfg, with_tb)).run()
        g.source_hash = hsh
        _MEM_CACHE[key] = g
        try:
            cache_f.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_f.with_suffix(".tmp")
            tmp.write_bytes(pickle.dumps(g, protocol=pickle.HIGHEST_PROTOCOL))
            os.replace(tmp, cache_f)
        except OSError:
            pass
        return g


def build_from_files(files: list[Path], *, top: str, root: Optional[Path] = None,
                     include: list[Path] = (), defines: Optional[dict] = None, reset: str = "reset") -> DesignGraph:
    """Uncached build from an explicit file list (tests, tools)."""
    class _C:  # minimal cfg-like shim
        pass
    c = _C()
    c.root = Path(root or Path(files[0]).parent)
    c.project = type("p", (), {"top": top, "tb_top": None, "include": [], "defines": defines or {},
                               "reset": type("r", (), {"name": reset})()})()
    return _Extractor(c, list(files), include=list(include)).run()


def lookup_type(cfg: Config, signal_path: str) -> Optional[dict[str, Any]]:
    """Decode info for a waveform signal: ``{kind: "enum"|"struct"|"plain", enum?: {value: name},
    fields?: [{name, msb, lsb, enum?}]}``. Enum keys are decimal strings of the value. Nested struct
    fields are flattened with dotted names; msb/lsb are absolute bit positions in the signal.
    Returns None if the signal is unknown to the design."""
    try:
        g = build(cfg)
    except Exception:
        return None
    return graph_lookup_type(g, signal_path)


def graph_lookup_type(g: DesignGraph, signal_path: str) -> Optional[dict[str, Any]]:
    al = g.__dict__.get("_type_aligner")
    if al is None:
        al = PathAligner(g.signals.keys())
        g.__dict__["_type_aligner"] = al
    p = al.map(signal_path)
    if p is None:
        return None
    return g.types.get(p) or {"kind": "plain"}


# ------------------------------------------------------------------ extractor
_RESET_RE = re.compile(r"(^|_)(rst|reset|clr|clear)(_?n|_b|_l)?$", re.I)


class _Extractor:
    def __init__(self, cfg: Any, files: list[Path], include: Optional[list[Path]] = None):
        self.cfg = cfg
        self.root = Path(cfg.root).resolve()
        self.files = files
        try:
            self.include = include if include is not None else P.include_dirs(cfg)
        except Exception:
            self.include = []
        self.reset_name = getattr(getattr(cfg.project, "reset", None), "name", "reset")
        self.signals: dict[str, SignalInfo] = {}
        self.drivers: dict[str, list[Driver]] = {}
        self.instances: dict[str, dict] = {}
        self.modules: dict[str, dict] = {}
        self.types: dict[str, dict] = {}
        self.diags: list[str] = []
        self._files_cache: dict[Any, Optional[str]] = {}

    # -------------------------------------------------------------- compile
    def run(self) -> DesignGraph:
        import pyslang
        from pyslang import ast, driver

        self.ps, self.ast = pyslang, ast
        d = driver.Driver()
        d.addStandardArgs()
        args = ["slang", "--ignore-unknown-modules", "--timescale=1ns/1ps", "-Wnone"]
        for inc in self.include:
            args += ["-I", str(inc)]
        for k, v in (self.cfg.project.defines or {}).items():
            args.append(f"-D{k}" if v is True else f"-D{k}={int(v) if isinstance(v, bool) else v}")
        args += [str(f) for f in self.files]
        if not self.files:
            raise ValueError("no design sources resolved from [project].sources")
        cmd = " ".join(shlex.quote(a) for a in args)
        if not d.parseCommandLine(cmd, driver.CommandLineOptions()) or not d.processOptions():
            raise ValueError(f"slang rejected the command line: {cmd}")
        d.parseAllSources()
        comp = d.createCompilation()
        self.comp, self.sm = comp, comp.sourceManager
        root = comp.getRoot()
        try:
            diags = comp.getAllDiagnostics()
            errs = [x for x in diags if x.isError()]
            if errs:
                txt = pyslang.DiagnosticEngine.reportAll(self.sm, errs)
                self.diags = [ln for ln in txt.splitlines() if ": error:" in ln][:200]
        except Exception as e:  # diagnostics are informative only
            self.diags.append(f"diagnostics unavailable: {e}")
        roots = []
        for inst in root.topInstances:
            roots.append(inst.hierarchicalPath)
            self._instance(inst, None)
        top = self.cfg.project.top
        design_tops = sorted((p for p, i in self.instances.items() if i["module"] == top),
                             key=lambda p: (p.count("."), p))
        for s in self.signals.values():
            if s.scope == "":
                s.scope = s.path.rsplit(".", 1)[0]
        for path, drvs in self.drivers.items():
            for dr in drvs:
                self._summarize(dr)
        return DesignGraph(top_module=top, roots=roots, design_tops=design_tops, instances=self.instances,
                           modules=self.modules, signals=self.signals, drivers=self.drivers, types=self.types,
                           diagnostics=self.diags)

    # -------------------------------------------------------------- locations
    def _file(self, loc) -> Optional[str]:
        try:
            buf = loc.buffer
            key = buf.id if hasattr(buf, "id") else str(buf)
            if key in self._files_cache:
                return self._files_cache[key]
            f = self.sm.getFullPath(buf)
            s = str(f) if f else None
            if s:
                p = Path(s).resolve()
                s = str(p.relative_to(self.root)) if p.is_relative_to(self.root) else str(p)
            self._files_cache[key] = s
            return s
        except Exception:
            return None

    def _line(self, loc) -> Optional[int]:
        try:
            return int(self.sm.getLineNumber(loc))
        except Exception:
            return None

    @staticmethod
    def _text(node) -> str:
        try:
            t = "" if node.syntax is None else str(node.syntax)
        except Exception:
            return ""
        t = " ".join(t.strip().split())
        return t[:160]

    # -------------------------------------------------------------- hierarchy
    def _instance(self, inst, parent: Optional[str]) -> None:
        ast = self.ast
        path = inst.hierarchicalPath
        defn = inst.definition
        mname = defn.name
        conns = []
        if parent is not None:
            try:
                conns = self._conns(inst, parent, path)
            except Exception as e:
                self.diags.append(f"{path}: port connections not understood ({e})")
        self.instances[path] = {"module": mname, "parent": parent, "file": self._file(inst.location),
                                "line": self._line(inst.location), "conns": conns}
        body = inst.body
        if mname not in self.modules:
            ports = []
            for pt in getattr(body, "portList", []) or []:
                try:
                    ports.append({"name": pt.name, "dir": _dir(pt.direction), "width": pt.type.bitWidth,
                                  "type": str(pt.type)})
                except Exception:
                    ports.append({"name": getattr(pt, "name", "?"), "dir": "inout", "width": None, "type": "?"})
            self.modules[mname] = {"file": self._file(defn.location), "line": self._line(defn.location),
                                   "ports": ports, "instances": []}
            self._mod_first = getattr(self, "_mod_first", {})
            self._mod_first[mname] = path
        first = getattr(self, "_mod_first", {}).get(mname) == path
        self._scope(body, path, first_of_module=first, module=mname)

    def _conns(self, inst, parent: str, path: str) -> list[dict]:
        ast = self.ast
        out = []
        for pc in inst.portConnections:
            port = pc.port
            if port.kind != ast.SymbolKind.Port:
                continue
            dirn = _dir(port.direction)
            internal = port.internalSymbol
            ipath = internal.hierarchicalPath if internal is not None else f"{path}.{port.name}"
            e = pc.expression
            line = self._line(inst.location)
            fname = self._file(inst.location)
            text = f".{port.name}(...) of {path.rsplit('.', 1)[-1]}"
            try:
                ee = e.left if (e is not None and e.kind == self.ast.ExpressionKind.Assignment) else e
                text = f".{port.name}({self._text(ee) if ee is not None else ''}) in {path.rsplit('.', 1)[-1]}"
            except Exception:
                pass
            sigs: list[str] = []
            if e is None:
                out.append({"port": port.name, "dir": dirn, "signals": [], "internal": ipath})
                continue
            if dirn == "in" or dirn == "inout":
                rhs = self._expr(e)
                sigs = list(dict.fromkeys(X.signals(rhs)))
                if internal is not None:
                    self._sig(internal, path)
                    self._add_driver(ipath, Driver("port", fname, line, text, path,
                                                   [Assign(ipath, rhs, line=line, text=text)]))
            if dirn == "out":
                lv = e.left if e.kind == ast.ExpressionKind.Assignment else e
                rhs = ("s", ipath, port.type.bitWidth or 0)
                if internal is not None:
                    self._sig(internal, path)
                for tgt, partial, idx in self._lvalues(lv):
                    sigs.append(tgt)
                    self._add_driver(tgt, Driver("port", fname, line, text, parent,
                                                 [Assign(tgt, rhs, partial=partial, index=idx, line=line,
                                                         text=text)]))
            out.append({"port": port.name, "dir": dirn, "signals": list(dict.fromkeys(sigs)), "internal": ipath})
        return out

    def _scope(self, scope, inst_path: str, *, first_of_module: bool, module: str) -> None:
        ast = self.ast
        SK = ast.SymbolKind
        for m in scope:
            k = m.kind
            try:
                if k in (SK.Variable, SK.Net):
                    self._sig(m, inst_path)
                elif k == SK.Port:
                    s = m.internalSymbol
                    if s is not None:
                        si = self._sig(s, inst_path)
                        si.port = _dir(m.direction)
                elif k == SK.ContinuousAssign:
                    self._cont_assign(m, inst_path)
                elif k == SK.ProceduralBlock:
                    self._proc(m, inst_path)
                elif k == SK.Instance:
                    if first_of_module:
                        self.modules[module]["instances"].append({"name": m.name, "module": m.definition.name})
                    self._instance(m, inst_path)
                elif k == SK.InstanceArray:
                    for el in self._array_elems(m):
                        if first_of_module:
                            self.modules[module]["instances"].append(
                                {"name": el.hierarchicalPath.rsplit(".", 1)[-1], "module": el.definition.name})
                        self._instance(el, inst_path)
                elif k == SK.GenerateBlock:
                    if not m.isUninstantiated:
                        self._scope(m, inst_path, first_of_module=first_of_module, module=module)
                elif k == SK.GenerateBlockArray:
                    for e in m.entries:
                        if not e.isUninstantiated:
                            self._scope(e, inst_path, first_of_module=first_of_module, module=module)
            except Exception as e:  # never crash on an unsupported construct
                self.diags.append(f"{inst_path}: {k} not understood ({type(e).__name__}: {e})")

    def _array_elems(self, arr):
        for el in arr.elements:
            if el.kind == self.ast.SymbolKind.InstanceArray:
                yield from self._array_elems(el)
            elif el.kind == self.ast.SymbolKind.Instance:
                yield el

    # -------------------------------------------------------------- signals / types
    def _sig(self, sym, inst_path: str) -> SignalInfo:
        path = sym.hierarchicalPath
        si = self.signals.get(path)
        if si is not None:
            return si
        ty = sym.type
        ct = ty.canonicalType
        mem = None
        ew = None
        try:
            if ct.isUnpackedArray and ct.hasFixedRange:
                r = ct.fixedRange
                mem = (min(r.left, r.right), max(r.left, r.right))
                ew = ct.elementType.bitWidth
        except Exception:
            pass
        kind = "net" if sym.kind == self.ast.SymbolKind.Net else "var"
        si = SignalInfo(path=path, width=int(ty.bitWidth or 0), kind=kind, type=str(ty),
                        file=self._file(sym.location), line=self._line(sym.location), mem=mem, elem_width=ew,
                        scope=inst_path)
        self.signals[path] = si
        try:
            info = self._typeinfo(ct)
            if info:
                self.types[path] = info
        except Exception:
            pass
        return si

    def _typeinfo(self, ct) -> Optional[dict]:
        if ct.isEnum:
            return {"kind": "enum", "enum": self._enum(ct)}
        if ct.isStruct and not ct.isUnpackedStruct:
            fields: list[dict] = []
            self._fields(ct, "", 0, fields)
            return {"kind": "struct", "fields": fields}
        return None

    def _enum(self, ct) -> dict[str, str]:
        out = {}
        for v in ct:
            if v.kind == self.ast.SymbolKind.EnumValue:
                try:
                    out[str(v.value.value.toString(self.ps.LiteralBase.Decimal, False))] = v.name
                except Exception:
                    try:
                        out[str(v.value.convertToInt())] = v.name
                    except Exception:
                        pass
        return out

    def _fields(self, ct, prefix: str, base: int, out: list) -> None:
        for f in ct:
            if f.kind != self.ast.SymbolKind.Field:
                continue
            fct = f.type.canonicalType
            w = int(f.type.bitWidth)
            lsb = base + int(f.bitOffset)
            name = prefix + f.name
            ent: dict[str, Any] = {"name": name, "msb": lsb + w - 1, "lsb": lsb}
            if fct.isEnum:
                ent["enum"] = self._enum(fct)
            out.append(ent)
            if fct.isStruct and not fct.isUnpackedStruct:
                self._fields(fct, name + ".", lsb, out)

    # -------------------------------------------------------------- drivers
    def _add_driver(self, path: str, d: Driver) -> None:
        lst = self.drivers.setdefault(path, [])
        lst.append(d)

    def _cont_assign(self, m, inst_path: str) -> None:
        a = m.assignment
        line = self._line(m.location)
        text = self._text(m)
        if not text.startswith("assign"):
            text = "assign " + text
        rhs = self._expr(a.right)
        for tgt, partial, idx in self._lvalues(a.left):
            self._add_driver(tgt, Driver("assign", self._file(m.location), line, text, inst_path,
                                         [Assign(tgt, rhs, partial=partial, index=idx, line=line, text=text)]))

    def _proc(self, m, inst_path: str) -> None:
        ast = self.ast
        PK = ast.ProceduralBlockKind
        pk = m.procedureKind
        body = m.body
        timing = None
        if body.kind == ast.StatementKind.Timed:
            timing = body.timing
            body = body.stmt
        events = self._events(timing) if timing is not None else []
        if pk == PK.AlwaysFF:
            kind = "always_ff"
        elif pk == PK.AlwaysComb:
            kind = "always_comb"
        elif pk == PK.AlwaysLatch:
            kind = "always_latch"
        elif pk in (PK.Initial,):
            kind = "initial"
        elif pk == PK.Always:
            kind = "always_ff" if any(e[1] in ("posedge", "negedge") for e in events) else "always_comb"
        else:
            return
        text = self._text(m).split(" begin")[0][:120]
        file, line = self._file(m.location), self._line(m.location)
        assigns: list[Assign] = []
        reset = reset_active = None
        has_reset = False
        clock = edge = None
        if kind == "always_ff":
            top = _unwrap(body, ast)
            rinfo = self._reset_branch(top, events) if top is not None else None
            if rinfo:
                reset, reset_active = rinfo
                has_reset = True
                cond = self._expr(_cond_expr(top))
                self._stmt(top.ifTrue, (( cond, True),), assigns, in_reset=True)
                if top.ifFalse is not None:
                    self._stmt(top.ifFalse, ((cond, False),), assigns)
            else:
                self._stmt(body, (), assigns)
            clk_ev = [e for e in events if e[0] != reset] or events
            if clk_ev:
                clock, edge = clk_ev[0]
        else:
            self._stmt(body, (), assigns)
        by_tgt: dict[str, list[Assign]] = {}
        for a in assigns:
            by_tgt.setdefault(a.lhs, []).append(a)
        for tgt, lst in by_tgt.items():
            self._add_driver(tgt, Driver(kind, file, line, text, inst_path, lst, clock=clock, edge=edge,
                                         reset=reset, reset_active=reset_active, has_reset_branch=has_reset))

    def _events(self, tc) -> list[tuple[str, str]]:
        ast = self.ast
        TK = ast.TimingControlKind
        out = []
        evs = tc.events if tc.kind == TK.EventList else [tc]
        for ev in evs:
            if ev.kind != TK.SignalEvent:
                continue
            e = ev.expr
            sym = _root_symbol(e, ast)
            if sym is None:
                continue
            edge = {ast.EdgeKind.PosEdge: "posedge", ast.EdgeKind.NegEdge: "negedge"}.get(ev.edge, "edge")
            out.append((sym.hierarchicalPath, edge))
        return out

    def _reset_branch(self, stmt, events) -> Optional[tuple[str, str]]:
        """Recognise `if (rst) ... else ...` / `if (!rst_n) ...` as the reset branch of an always_ff."""
        ast = self.ast
        if stmt.kind != ast.StatementKind.Conditional:
            return None
        c = _cond_expr(stmt)
        if c is None:
            return None
        neg = False
        e = _strip_conv(c, ast)
        if e.kind == ast.ExpressionKind.UnaryOp and e.op.name in ("LogicalNot", "BitwiseNot"):
            neg, e = True, _strip_conv(e.operand, ast)
        elif e.kind == ast.ExpressionKind.BinaryOp and e.op.name in ("Equality", "CaseEquality"):
            lit = e.right
            try:
                v = lit.eval(self.ast.EvalContext(self.comp.getRoot()))
                neg = bool(v) and v.value.toString(self.ps.LiteralBase.Decimal, False) == "0"
            except Exception:
                return None
            e = _strip_conv(e.left, ast)
        if e.kind not in (ast.ExpressionKind.NamedValue, ast.ExpressionKind.HierarchicalValue):
            return None
        path = e.symbol.hierarchicalPath
        name = e.symbol.name
        in_events = any(p == path for p, _ in events)
        cfg_name = self.reset_name or ""
        if in_events or name == cfg_name or _RESET_RE.search(name):
            return path, ("low" if neg else "high")
        return None

    def _stmt(self, s, guards: tuple, out: list, in_reset: bool = False) -> None:
        ast = self.ast
        SK = ast.StatementKind
        k = s.kind
        if k == SK.Block:
            self._stmt(s.body, guards, out, in_reset)
        elif k == SK.List:
            for x in s.list:
                self._stmt(x, guards, out, in_reset)
        elif k == SK.ExpressionStatement:
            self._expr_stmt(s.expr, guards, out, in_reset, s)
        elif k == SK.Conditional:
            conds = [cc.expr for cc in s.conditions]
            if len(conds) == 1 and conds[0] is not None and getattr(s.conditions[0], "pattern", None) is None:
                c = self._expr(conds[0])
            else:
                c = ("x", tuple(self._expr(x) for x in conds if x is not None), 1)
            self._stmt(s.ifTrue, guards + ((c, True),), out, in_reset)
            if s.ifFalse is not None:
                self._stmt(s.ifFalse, guards + ((c, False),), out, in_reset)
        elif k == SK.Case:
            sel = self._expr(s.expr)
            normal = s.condition == ast.CaseStatementCondition.Normal
            prev: list = []
            for it in s.items:
                if normal:
                    terms = [("b", "Equality", sel, self._expr(x), 1) for x in it.expressions]
                    cond = terms[0] if len(terms) == 1 else ("x", tuple(terms), 1) if not terms else \
                        _or_chain(terms)
                else:
                    cond = ("x", (sel, *(self._expr(x) for x in it.expressions)), 1)
                g = guards + tuple((p, False) for p in prev) + ((cond, True),)
                self._stmt(it.stmt, g, out, in_reset)
                prev.append(cond)
            if s.defaultCase is not None:
                g = guards + tuple((p, False) for p in prev)
                self._stmt(s.defaultCase, g, out, in_reset)
        elif k == SK.Timed:
            self._stmt(s.stmt, guards, out, in_reset)
        elif k in (SK.ForLoop, SK.ForeachLoop, SK.RepeatLoop, SK.WhileLoop, SK.DoWhileLoop, SK.ForeverLoop):
            self._stmt(s.body, guards, out, in_reset)
        elif k == SK.VariableDeclaration:
            v = s.symbol
            init = getattr(v, "initializer", None)
            if init is not None:
                p = v.hierarchicalPath
                out.append(Assign(p, self._expr(init), guards, line=self._line(v.location), text=self._text(s),
                                  in_reset=in_reset))
        elif k in (SK.Empty, SK.Return, SK.Break, SK.Continue, SK.Disable, SK.EventTrigger,
                   SK.ImmediateAssertion, SK.ConcurrentAssertion, SK.WaitFork, SK.DisableFork):
            return
        else:
            # unsupported statement: still find assignments inside it so drivers are not lost
            found = []

            def cb(x):
                if isinstance(x, ast.Expression) and x.kind == ast.ExpressionKind.Assignment:
                    found.append(x)
                return ast.VisitAction.Advance
            try:
                s.visit(cb)
            except Exception:
                pass
            for a in found:
                self._expr_stmt(a, guards + ((("x", (), 1), True),), out, in_reset, s)

    def _expr_stmt(self, e, guards, out, in_reset, stmt) -> None:
        ast = self.ast
        EK = ast.ExpressionKind
        line = self._line(stmt.sourceRange.start)
        text = self._text(stmt)
        if e.kind == EK.Assignment:
            rhs = self._expr(e.right)
            for tgt, partial, idx in self._lvalues(e.left):
                out.append(Assign(tgt, rhs, guards, idx, partial, line, text, in_reset))
        elif e.kind == EK.Call and str(getattr(e, "subroutineName", "")).startswith("$readmem"):
            args = list(e.arguments)
            if len(args) >= 2:      # $readmemh(file, mem): the memory is initialized from a file
                m = args[1].left if args[1].kind == EK.Assignment else args[1]
                for tgt, _, _ in self._lvalues(m):
                    out.append(Assign(tgt, ("x", (), 0), guards, None, False, line, text, in_reset))
        elif e.kind == EK.UnaryOp and e.op.name in ("Preincrement", "Predecrement", "Postincrement",
                                                     "Postdecrement"):
            for tgt, partial, idx in self._lvalues(e.operand):
                out.append(Assign(tgt, self._expr(e.operand), guards, idx, partial, line, text, in_reset))

    def _lvalues(self, lv) -> list[tuple[str, bool, Optional[tuple]]]:
        """Targets of an lvalue: [(signal path, partial, memory index expr)]."""
        ast = self.ast
        EK = ast.ExpressionKind
        k = lv.kind
        if k in (EK.NamedValue, EK.HierarchicalValue):
            return [(lv.symbol.hierarchicalPath, False, None)]
        if k == EK.Concatenation:
            out = []
            for op in lv.operands:
                out += [(t, True, i) for t, _, i in self._lvalues(op)]
            return out
        if k == EK.ElementSelect:
            base = lv.value
            if base.kind in (EK.NamedValue, EK.HierarchicalValue) and base.type.canonicalType.isUnpackedArray:
                return [(base.symbol.hierarchicalPath, False, self._expr(lv.selector))]
            return [(t, True, i) for t, _, i in self._lvalues(base)]
        if k in (EK.RangeSelect, EK.MemberAccess):
            return [(t, True, i) for t, _, i in self._lvalues(lv.value)]
        if k == EK.Conversion:
            return self._lvalues(lv.operand)
        sym = _root_symbol(lv, ast)
        return [(sym.hierarchicalPath, True, None)] if sym is not None else []

    # -------------------------------------------------------------- expressions
    def _const(self, e) -> Optional[tuple]:
        try:
            if not hasattr(self, "_ectx"):
                self._ectx = self.ast.EvalContext(self.comp.getRoot())
            v = e.eval(self._ectx)
            if not v:
                return None
            sv = v.value
            if not isinstance(sv, self.ps.SVInt):
                return None
            w = int(sv.bitWidth)
            s = sv.toString(self.ps.LiteralBase.Binary, False)
            s = s.split("'")[-1].lstrip("sbBS")
            s = s.lower().replace("?", "z")
            if len(s) < w:
                s = (s[0] if s and s[0] in "xz" else "0") * (w - len(s)) + s
            return ("c", s[-w:] if w else s, w)
        except Exception:
            return None

    def _expr(self, e) -> tuple:
        try:
            return self._expr_(e)
        except Exception:
            return ("x", tuple(("s", p, 0) for p in self._refs(e)), _w(e))

    def _refs(self, e) -> list[str]:
        ast = self.ast
        out = []

        def cb(x):
            if isinstance(x, ast.Expression) and x.kind in (ast.ExpressionKind.NamedValue,
                                                             ast.ExpressionKind.HierarchicalValue):
                sk = x.symbol.kind
                if sk in (ast.SymbolKind.Variable, ast.SymbolKind.Net):
                    out.append(x.symbol.hierarchicalPath)
            return ast.VisitAction.Advance
        try:
            e.visit(cb)
        except Exception:
            pass
        return list(dict.fromkeys(out))

    def _expr_(self, e) -> tuple:
        ast = self.ast
        EK = ast.ExpressionKind
        k = e.kind
        w = _w(e)
        if k in (EK.NamedValue, EK.HierarchicalValue):
            sym = e.symbol
            if sym.kind in (ast.SymbolKind.Variable, ast.SymbolKind.Net):
                ct = sym.type.canonicalType
                return ("s", sym.hierarchicalPath, 0 if ct.isUnpackedArray or not ct.isIntegral else w)
            c = self._const(e)
            if c is not None:
                return c
            return ("x", (), w)
        c = self._const(e) if k not in (EK.Assignment, EK.Call) else None
        if c is not None:
            return c
        if k in (EK.IntegerLiteral, EK.UnbasedUnsizedIntegerLiteral):
            return ("x", (), w)
        if k == EK.Conversion:
            return ("cv", self._expr(e.operand), w)
        if k == EK.UnaryOp:
            return ("u", e.op.name, self._expr(e.operand), w)
        if k == EK.BinaryOp:
            return ("b", e.op.name, self._expr(e.left), self._expr(e.right), w)
        if k == EK.ConditionalOp:
            conds = [cc.expr for cc in e.conditions]
            if len(conds) == 1 and getattr(e.conditions[0], "pattern", None) is None:
                cnd = self._expr(conds[0])
            else:
                cnd = ("x", tuple(self._expr(x) for x in conds if x is not None), 1)
            return ("?", cnd, self._expr(e.left), self._expr(e.right), w)
        if k == EK.Concatenation:
            return ("cat", tuple(self._expr(o) for o in e.operands), w)
        if k == EK.Replication:
            cnt = self._const(e.count)
            n = X.to_int(cnt[1]) if cnt else None
            if n is None:
                return ("x", (self._expr(e.concat),), w)
            return ("rep", n, self._expr(e.concat), w)
        if k == EK.ElementSelect:
            base = e.value
            bct = base.type.canonicalType
            if bct.isUnpackedArray:
                if base.kind in (EK.NamedValue, EK.HierarchicalValue) and bct.hasFixedRange:
                    r = bct.fixedRange
                    return ("m", base.symbol.hierarchicalPath, self._expr(e.selector), w,
                            min(r.left, r.right), max(r.left, r.right))
                return ("x", (self._expr(base), self._expr(e.selector)), w)
            rng = _range(bct)
            if rng is None:
                return ("x", (self._expr(base), self._expr(e.selector)), w)
            left, right = rng
            return ("ps", self._expr(base), self._expr(e.selector), w, 0, right, left < right, w)
        if k == EK.RangeSelect:
            base = e.value
            bct = base.type.canonicalType
            rng = _range(bct)
            if rng is None or bct.isUnpackedArray:
                return ("x", (self._expr(base), self._expr(e.left), self._expr(e.right)), w)
            left, right = rng
            asc = left < right
            elems = max(1, abs(left - right) + 1)
            ew = max(1, int(bct.bitWidth) // elems)
            n = max(1, w // ew)
            sk = e.selectionKind.name
            if sk == "Simple":
                return ("ps", self._expr(base), self._expr(e.right), w, 0, right, asc, ew)
            start = self._expr(e.left)
            if sk == "IndexedUp":
                add = (n - 1) if asc else 0
            else:  # IndexedDown
                add = 0 if asc else -(n - 1)
            return ("ps", self._expr(base), start, w, add, right, asc, ew)
        if k == EK.MemberAccess:
            base = e.value
            bct = base.type.canonicalType
            mem = e.member
            if (bct.isStruct and not bct.isUnpackedStruct) or getattr(bct, "isPackedUnion", False):
                if mem.kind == ast.SymbolKind.Field:
                    return ("rng", self._expr(base), int(mem.bitOffset), w)
            return ("x", (self._expr(base),), w)
        refs = self._refs(e)
        return ("x", tuple(("s", p, 0) for p in refs), w)

    # -------------------------------------------------------------- summary
    @staticmethod
    def _summarize(d: Driver) -> None:
        data, ctrl, index = [], [], []
        for a in d.assigns:
            data += list(_data_signals(a.rhs))
            index += [s for s in X.index_signals(a.rhs)]
            if a.index is not None:
                index += list(X.signals(a.index))
            for g, _ in a.guards:
                ctrl += list(X.signals(g))
            ctrl += list(_ternary_selects(a.rhs))
        idx = set(index)
        d.index = list(dict.fromkeys(index))
        d.data = [s for s in dict.fromkeys(data) if s not in idx or s in _mem_bases(d)]
        d.ctrl = list(dict.fromkeys(ctrl))
        if d.clock:
            d.ctrl = [c for c in d.ctrl if c != d.clock]


def _mem_bases(d: Driver) -> set:
    out = set()
    for a in d.assigns:
        _collect_mem(a.rhs, out)
    return out


def _collect_mem(e: tuple, out: set) -> None:
    if e[0] == "m":
        out.add(e[1])
    for c in X.children(e):
        _collect_mem(c, out)


def _data_signals(e: tuple):
    """Signals read as data: like expr.signals() but skipping ternary selects (those are control)."""
    if e[0] == "?":
        yield from _data_signals(e[2])
        yield from _data_signals(e[3])
        return
    if e[0] == "s":
        yield e[1]
        return
    if e[0] == "m":
        yield e[1]
        yield from X.signals(e[2])
        return
    for c in X.children(e):
        yield from _data_signals(c)


def _ternary_selects(e: tuple):
    if e[0] == "?":
        yield from X.signals(e[1])
    for c in X.children(e):
        yield from _ternary_selects(c)


def _or_chain(terms: list) -> tuple:
    acc = terms[0]
    for t in terms[1:]:
        acc = ("b", "LogicalOr", acc, t, 1)
    return acc


def _dir(d) -> str:
    n = getattr(d, "name", str(d))
    return {"In": "in", "Out": "out", "InOut": "inout", "Ref": "inout"}.get(n, "inout")


def _w(e) -> int:
    try:
        return int(e.type.bitWidth or 0)
    except Exception:
        return 0


def _range(ct) -> Optional[tuple[int, int]]:
    try:
        r = ct.getBitVectorRange() if ct.isIntegral else ct.fixedRange
        return int(r.left), int(r.right)
    except Exception:
        try:
            r = ct.fixedRange
            return int(r.left), int(r.right)
        except Exception:
            return None


def _unwrap(s, ast):
    """Strip begin/end wrappers with exactly one statement."""
    while s is not None:
        if s.kind == ast.StatementKind.Block:
            s = s.body
        elif s.kind == ast.StatementKind.List and len(s.list) == 1:
            s = s.list[0]
        else:
            return s
    return s


def _cond_expr(stmt):
    try:
        conds = stmt.conditions
        return conds[0].expr if len(conds) == 1 else None
    except Exception:
        return None


def _strip_conv(e, ast):
    while e is not None and e.kind == ast.ExpressionKind.Conversion:
        e = e.operand
    return e


def _root_symbol(e, ast):
    EK = ast.ExpressionKind
    while e is not None:
        k = e.kind
        if k in (EK.NamedValue, EK.HierarchicalValue):
            return e.symbol
        if k in (EK.ElementSelect, EK.RangeSelect, EK.MemberAccess):
            e = e.value
        elif k == EK.Conversion:
            e = e.operand
        else:
            return None
    return None
