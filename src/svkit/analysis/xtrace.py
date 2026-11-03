"""X-propagation root-cause tracer (MVP: standard Verilog X semantics, signal granularity).

Starting from ``(signal, time)`` where the waveform shows X/Z, walk the DesignGraph's drivers
backwards:

* combinational drivers (``assign``, ``always_comb``, port connections) are evaluated at ``time``;
  of an ``always_comb`` only the assignment that actually executed (guards evaluated against the
  waveform) is followed, and of a ternary only the selected branch when the select is known;
* sequential drivers (``always_ff``) are evaluated just before the previous active clock edge;
  when the flop held its value, the trace jumps to the start of the X stretch;
* recursion goes into every input whose value contains X (or is not dumped).

Root causes (XCause): ``no_reset`` (register X since time 0 and not reset / never assigned),
``uninit_memory`` (memory word read but never written), ``index_out_of_range``,
``external_input`` (top-level input port of the design top, or a testbench-driven signal),
``unknown`` (depth / budget bound, unsupported construct).

VCS ``-xprop=tmerge`` semantics are post-MVP; an X ``if`` condition here takes the else branch.
"""
from __future__ import annotations

import bisect
from pathlib import Path
from typing import Any, Callable, Optional

from svkit.design import expr as E
from svkit.design.graph import Assign, DesignGraph, Driver, PathAligner
from svkit.design.wavesrc import WaveSource
from svkit.schemas import DriverRef, XCause, XTraceNode

SEQ_KINDS = ("always_ff",)
MAX_HOLD_JUMPS = 64


class Tracer:
    def __init__(self, graph: DesignGraph, wave: WaveSource, *, max_nodes: int = 400,
                 max_write_scan: int = 200_000):
        self.g = graph
        self.w = wave
        self.max_nodes = max_nodes
        self.max_write_scan = max_write_scan
        self.g2w = PathAligner(wave.vars())
        self.w2g = PathAligner(graph.signals.keys())
        self.memo: dict[tuple[str, int], XTraceNode] = {}
        self.active: set[tuple[str, int]] = set()
        self.nodes = 0
        self._edges: dict[tuple[str, str], list[int]] = {}
        self._vcache: dict[tuple[str, int], Optional[str]] = {}

    # -------------------------------------------------------------- values
    def wname(self, gpath: str) -> Optional[str]:
        return self.g2w.map(gpath)

    def val(self, gpath: str, t: int) -> Optional[str]:
        key = (gpath, t)
        if key in self._vcache:
            return self._vcache[key]
        wn = self.wname(gpath)
        v = self.w.value_at(wn, t) if wn else None
        self._vcache[key] = v
        return v

    def mem_word_name(self, mem: str, idx: int) -> Optional[str]:
        wn = self.wname(mem)
        cands = []
        if wn:
            cands += [f"{wn}[{idx}]", f"{wn}({idx})"]
        for c in cands:
            if self.w.has(c):
                return c
        for suffix in (f"[{idx}]", f"({idx})"):
            m = self.g2w.map(mem + suffix)
            if m:
                return m
        return None

    def mem_word(self, mem: str, idx: int, t: int) -> Optional[str]:
        n = self.mem_word_name(mem, idx)
        return self.w.value_at(n, t) if n else None

    def ev(self, t: int) -> E.Evaluator:
        return E.Evaluator(lambda p: self.val(p, t), lambda m, i: self.mem_word(m, i, t))

    def edges(self, clock: str, edge: str) -> list[int]:
        key = (clock, edge or "posedge")
        if key not in self._edges:
            wn = self.wname(clock)
            out: list[int] = []
            if wn:
                prev = None
                for t, v in self.w.changes(wn, 0, 1 << 62):
                    b = v[-1:].lower()
                    p = prev
                    prev = b
                    if p is None or p == b:
                        continue
                    if edge == "negedge":
                        hit = b == "0"
                    elif edge == "edge":
                        hit = True
                    else:
                        hit = b == "1"
                    if hit:
                        out.append(t)
            self._edges[key] = out
        return self._edges[key]

    def prev_edge(self, clock: str, edge: str, t: int) -> Optional[int]:
        es = self.edges(clock, edge)
        i = bisect.bisect_right(es, t) - 1
        return es[i] if i >= 0 else None

    def x_since(self, gpath: str, t: int) -> Optional[int]:
        """Start of the X stretch containing t: time of the first change after the last X-free value.
        Returns None if the signal has contained X since the start of the waveform."""
        wn = self.wname(gpath)
        if not wn:
            return None
        ch = self.w.changes(wn, 0, t)
        start = None
        for i in range(len(ch) - 1, -1, -1):
            if not E.has_x(ch[i][1]):
                return ch[i + 1][0] if i + 1 < len(ch) else t
            start = ch[i][0]
        return None if start is None or start <= (ch[0][0] if ch else 0) else start

    # -------------------------------------------------------------- entry point
    def trace(self, signal: str, time: int, depth: int = 12) -> XTraceNode:
        gp = signal if signal in self.g.signals else self.w2g.map(signal)
        if gp is None:
            v = self.w.value_at(signal, time) if self.w.has(signal) else None
            return XTraceNode(signal=signal, time=time, value=_fmt(v), cause=XCause.unknown,
                              note="signal not found in the design (check the hierarchical name)")
        v = self.val(gp, time)
        if v is not None and not E.has_x(v):
            return XTraceNode(signal=self.wname(gp) or gp, time=time, value=_fmt(v),
                              driver=self._dref(self.g.drivers.get(gp, [None])[0]),
                              note="no X/Z on this signal at this time - nothing to trace")
        return self.node(gp, time, depth)

    # -------------------------------------------------------------- nodes
    def _mk(self, gp: str, t: int, v: Optional[str], **kw) -> XTraceNode:
        self.nodes += 1
        return XTraceNode(signal=self.wname(gp) or gp, time=t, value=_fmt(v), **kw)

    def node(self, gp: str, t: int, depth: int) -> XTraceNode:
        key = (gp, t)
        if key in self.memo:
            m = self.memo[key]
            return XTraceNode(signal=m.signal, time=m.time, value=m.value, driver=m.driver, cause=m.cause,
                              note="(already traced above)" if not m.cause else m.note)
        v = self.val(gp, t)
        if key in self.active:
            return self._mk(gp, t, v, cause=XCause.unknown, note="combinational loop")
        if depth <= 0 or self.nodes >= self.max_nodes:
            return self._mk(gp, t, v, cause=XCause.unknown,
                            note="depth bound reached" if depth <= 0 else "node budget exhausted")
        self.active.add(key)
        try:
            n = self._node(gp, t, v, depth)
        finally:
            self.active.discard(key)
        self.memo[key] = n
        return n

    def _node(self, gp: str, t: int, v: Optional[str], depth: int) -> XTraceNode:
        g = self.g
        drivers = g.drivers.get(gp, [])
        from_design = drivers and all(d.kind == "port" and any(g.in_design(s) for s in d.data) for d in drivers)
        if not g.in_design(gp) and not from_design:
            d = drivers[0] if drivers else None
            return self._mk(gp, t, v, driver=self._dref(d), cause=XCause.external_input,
                            note=f"{_xz(v)} driven by the testbench" + _where(d))
        if g.top_input(gp):
            src = ""
            if drivers:
                srcs = [s for d in drivers for s in d.data]
                if srcs:
                    src = f"; driven by {', '.join(self.wname(s) or s for s in srcs[:3])}"
            return self._mk(gp, t, v, driver=self._dref(drivers[0] if drivers else None, "input"),
                            cause=XCause.external_input,
                            note=f"{_xz(v)} enters through top-level input port "
                                 f"'{gp.rsplit('.', 1)[-1]}' of {g.top_module}{src}")
        si = g.signals.get(gp)
        if not drivers:
            if si is None:
                return self._mk(gp, t, v, cause=XCause.unknown, note="no driver information for this signal")
            what = "net has no driver (floating)" if si.kind == "net" else "variable is never assigned"
            return self._mk(gp, t, v, driver=DriverRef(kind="unknown", file=si.file, line=si.line,
                                                       text=f"declaration of {gp.rsplit('.', 1)[-1]}"),
                            cause=XCause.no_reset, note=what)
        seq = [d for d in drivers if d.kind in SEQ_KINDS]
        if seq:
            return self._seq(gp, t, v, seq[0], depth)
        return self._comb(gp, t, v, [d for d in drivers if d.kind != "initial"] or drivers, depth)

    # -------------------------------------------------------------- combinational
    def _comb(self, gp: str, t: int, v: Optional[str], drivers: list[Driver], depth: int) -> XTraceNode:
        ev = self.ev(t)
        deps, xguards, eff_all = [], [], []
        for d in drivers:
            eff, xg, ran = _effective(d.assigns, ev)
            eff_all += eff
            xguards += xg
            for a in eff:
                deps += E.x_deps(a.rhs, ev)
        deps = _uniq(deps)
        if not deps:
            deps = _uniq([dd for c in xguards for dd in E.x_deps(c, ev)])
        d0 = drivers[0]
        dref = self._dref(d0, assign=eff_all[-1] if len(eff_all) == 1 else None)
        if not eff_all and d0.kind in ("always_comb", "always_latch"):
            if deps:
                return self._mk(gp, t, v, driver=dref, note="no assignment executed; X in a condition",
                                children=self._kids(deps, t, depth))
            return self._mk(gp, t, v, driver=dref, cause=XCause.unknown,
                            note="no assignment executed at this time (latch-like hold) - class 5 is post-MVP")
        if not deps:
            return self._mk(gp, t, v, driver=dref, cause=XCause.unknown,
                            note="no X input found at this time (unsupported construct or undumped signal)")
        return self._mk(gp, t, v, driver=dref, children=self._kids(deps, t, depth))

    # -------------------------------------------------------------- sequential
    def _seq(self, gp: str, t: int, v: Optional[str], d: Driver, depth: int) -> XTraceNode:
        cur = t
        note = None
        for _ in range(MAX_HOLD_JUMPS):
            e = self.prev_edge(d.clock, d.edge, cur) if d.clock else None
            if e is None:
                return self._root_no_reset(gp, t, v, d, cur)
            pre = e - 1
            ev = self.ev(pre)
            eff, xg, ran = _effective(d.assigns, ev)
            if eff:
                deps = _uniq([dd for a in eff for dd in E.x_deps(a.rhs, ev)])
                self_dep = ("sig", gp) in deps
                deps = [x for x in deps if x != ("sig", gp)]
                if deps:
                    edge_note = f"sampled before {d.edge or 'posedge'} {_leaf(d.clock)} @ {e}"
                    return self._mk(gp, t, v, driver=self._dref(d, assign=eff[-1] if len(eff) == 1 else None),
                                    note=edge_note if note is None else f"{note}; {edge_note}",
                                    children=self._kids(deps, pre, depth))
                if not self_dep:
                    if xg:
                        cdeps = _uniq([dd for c in xg for dd in E.x_deps(c, ev)])
                        if cdeps:
                            return self._mk(gp, t, v, driver=self._dref(d), note=f"X condition before edge @ {e}",
                                            children=self._kids(cdeps, pre, depth))
                    return self._mk(gp, t, v, driver=self._dref(d, assign=eff[-1]), cause=XCause.unknown,
                                    note=f"assigned an X-free value at edge @ {e} yet X observed")
            # held (no assignment executed) or only depends on itself: go to the start of the X stretch
            vp = self.val(gp, pre)
            if vp is not None and not E.has_x(vp):
                return self._mk(gp, t, v, driver=self._dref(d), cause=XCause.unknown,
                                note=f"held an X-free value before edge @ {e} yet X observed")
            start = self.x_since(gp, pre)
            if start is None:
                return self._root_no_reset(gp, t, v, d, pre)
            if start >= cur:
                return self._mk(gp, t, v, driver=self._dref(d), cause=XCause.unknown,
                                note="could not locate when the X was loaded")
            note = f"holds X since {start}"
            cur = start
        return self._mk(gp, t, v, driver=self._dref(d), cause=XCause.unknown, note="hold-jump bound reached")

    def _root_no_reset(self, gp: str, t: int, v: Optional[str], d: Driver, upto: int) -> XTraceNode:
        name = _leaf(gp)
        reset_assigns = [a for a in d.assigns if a.in_reset]
        if d.has_reset_branch and reset_assigns:
            note = (f"register '{name}' has a reset branch but reset ({_leaf(d.reset)}) was not applied "
                    f"before {t}; X since time 0")
        elif d.has_reset_branch:
            note = (f"register '{name}' is not assigned in the reset branch of its always_ff "
                    f"(reset {_leaf(d.reset)}); X since time 0")
        else:
            note = f"register '{name}' has no reset; X since time 0 until first loaded"
        return self._mk(gp, t, v, driver=self._dref(d), cause=XCause.no_reset, note=note)

    # -------------------------------------------------------------- memory
    def mem_node(self, mem: str, idx: Optional[int], t: int, depth: int) -> XTraceNode:
        name = f"{mem}[{'?' if idx is None else idx}]"
        wn = self.mem_word_name(mem, idx) if idx is not None else None
        v = self.w.value_at(wn, t) if wn else None
        disp = wn or f"{self.wname(mem) or mem}[{'?' if idx is None else idx}]"
        key = (name, t)
        if key in self.memo:
            m = self.memo[key]
            return XTraceNode(signal=m.signal, time=m.time, value=m.value, driver=m.driver, cause=m.cause,
                              note=m.note if m.cause else "(already traced above)")
        si = self.g.signals.get(mem)
        drivers = [d for d in self.g.drivers.get(mem, [])]
        self.nodes += 1
        if idx is None:
            n = XTraceNode(signal=disp, time=t, value=_fmt(v), cause=XCause.unknown,
                           note="memory read with an index that cannot be evaluated")
            self.memo[key] = n
            return n
        if not self.g.in_design(mem):
            n = XTraceNode(signal=disp, time=t, value=_fmt(v), cause=XCause.external_input,
                           note="memory in the testbench")
            self.memo[key] = n
            return n
        if depth <= 0:
            return XTraceNode(signal=disp, time=t, value=_fmt(v), cause=XCause.unknown, note="depth bound reached")
        n = self._mem_writes(mem, idx, t, depth, disp, v, drivers, si)
        self.memo[key] = n
        return n

    def _mem_writes(self, mem, idx, t, depth, disp, v, drivers, si) -> XTraceNode:
        init = [d for d in drivers if d.kind == "initial"]
        writers = [d for d in drivers if d.kind in SEQ_KINDS and d.clock]
        comb = [d for d in drivers if d.kind not in SEQ_KINDS and d.kind != "initial"]
        # walk clock edges backwards looking for the last write to mem[idx]
        scanned = 0
        best = None   # (edge, driver, assign, pre)
        for d in writers:
            es = self.edges(d.clock, d.edge)
            i = bisect.bisect_right(es, t) - 1
            while i >= 0 and scanned < self.max_write_scan:
                e = es[i]
                if best is not None and e <= best[0]:
                    break
                scanned += 1
                pre = e - 1
                ev = self.ev(pre)
                hit = None
                for a in d.assigns:
                    if a.lhs != mem:
                        continue
                    st = _guards(a.guards, ev)[0]
                    if st is False:
                        continue
                    if a.index is None:
                        hit = a      # whole-array assignment (e.g. reset loop / aggregate)
                        continue
                    iv = E.to_int(ev.eval(a.index))
                    if iv is None or iv == idx:
                        hit = a
                if hit is not None:
                    best = (e, d, hit, pre)
                    break
                i -= 1
        if best is not None:
            e, d, a, pre = best
            ev = self.ev(pre)
            data = ev.eval(a.rhs)
            if data is None or E.has_x(data):
                deps = [x for x in E.x_deps(a.rhs, ev)]
                if deps:
                    return XTraceNode(signal=disp, time=t, value=_fmt(v), driver=self._dref(d, assign=a),
                                      note=f"{_leaf(mem)}[{idx}] written with X at edge @ {e}",
                                      children=self._kids(deps, pre, depth))
            if a.in_reset or (data is not None and not E.has_x(data)):
                return XTraceNode(signal=disp, time=t, value=_fmt(v), driver=self._dref(d, assign=a),
                                  cause=XCause.unknown,
                                  note=f"{_leaf(mem)}[{idx}] was written with an X-free value at @ {e}")
            return XTraceNode(signal=disp, time=t, value=_fmt(v), driver=self._dref(d, assign=a),
                              cause=XCause.unknown, note=f"{_leaf(mem)}[{idx}] last written at @ {e}")
        if scanned >= self.max_write_scan:
            return XTraceNode(signal=disp, time=t, value=_fmt(v), cause=XCause.unknown,
                              note=f"write-history scan bound ({self.max_write_scan} edges) reached")
        for d in comb:
            ev = self.ev(t)
            eff, _, _ = _effective(d.assigns, ev)
            deps = _uniq([dd for a in eff for dd in E.x_deps(a.rhs, ev)])
            if deps:
                return XTraceNode(signal=disp, time=t, value=_fmt(v), driver=self._dref(d),
                                  children=self._kids(deps, t, depth))
        if init:
            return XTraceNode(signal=disp, time=t, value=_fmt(v), driver=self._dref(init[0]), cause=XCause.unknown,
                              note=f"{_leaf(mem)} has an initial block but {_leaf(mem)}[{idx}] still reads X")
        dref = None
        if writers:
            dref = self._dref(writers[0])
        elif si is not None:
            dref = DriverRef(kind="unknown", file=si.file, line=si.line, text=f"declaration of {_leaf(mem)}")
        return XTraceNode(signal=disp, time=t, value=_fmt(v), driver=dref, cause=XCause.uninit_memory,
                          note=f"{_leaf(mem)}[{idx}] read at {t} was never written before "
                               f"(no reset or initial value for {_leaf(mem)})")

    # -------------------------------------------------------------- children
    def _kids(self, deps: list[tuple], t: int, depth: int) -> list[XTraceNode]:
        out = []
        for dp in deps:
            if dp[0] == "sig":
                if dp[1] not in self.g.signals and dp[1] not in self.g.drivers:
                    continue
                out.append(self.node(dp[1], t, depth - 1))
            elif dp[0] == "mem":
                out.append(self.mem_node(dp[1], dp[2], t, depth - 1))
            elif dp[0] == "oor":
                _, mem, idx, lo, hi = dp
                self.nodes += 1
                out.append(XTraceNode(signal=f"{self.wname(mem) or mem}[{idx}]", time=t, value="x",
                                      driver=self._mem_decl(mem), cause=XCause.index_out_of_range,
                                      note=f"index {idx} is outside {_leaf(mem)}[{lo}:{hi}]; "
                                           f"the read returns X"))
        return out

    def _mem_decl(self, mem: str) -> Optional[DriverRef]:
        si = self.g.signals.get(mem)
        if si is None:
            return None
        return DriverRef(kind="unknown", file=si.file, line=si.line, text=f"declaration of {_leaf(mem)} "
                         f"[{si.mem[0]}:{si.mem[1]}]" if si.mem else f"declaration of {_leaf(mem)}")

    @staticmethod
    def _dref(d: Optional[Driver], kind: Optional[str] = None, assign: Optional[Assign] = None) -> Optional[DriverRef]:
        if d is None:
            return DriverRef(kind=kind) if kind else None
        k = kind or d.kind
        if k not in ("assign", "always_comb", "always_ff", "always_latch", "port", "initial", "input", "unknown"):
            k = "unknown"
        if assign is not None and assign.line:
            return DriverRef(kind=k, file=d.file, line=assign.line, text=assign.text or d.text)
        return DriverRef(kind=k, file=d.file, line=d.line, text=d.text)


# ------------------------------------------------------------------ guards
def _guards(guards: tuple, ev: E.Evaluator) -> tuple[Any, list]:
    """Evaluate an assignment's guards: (True | False | 'maybe', [X-valued condition exprs])."""
    st: Any = True
    xs = []
    for c, pol in guards:
        tv = E.truth(ev.eval(c))
        if tv is None:
            st = "maybe" if st is True else st
            continue
        if tv == "x":
            xs.append(c)
            if pol:            # if (X) takes the else branch in standard semantics
                return False, xs
            continue
        if bool(tv) != pol:
            return False, xs
    return st, xs


def _effective(assigns: list[Assign], ev: E.Evaluator) -> tuple[list[Assign], list, bool]:
    """Assignments that determined the value: the last full assignment that executed plus any
    later partial / undecidable ones. Returns (effective, X-valued guard conditions, any_ran)."""
    executed, xg = [], []
    for i, a in enumerate(assigns):
        if a.index is not None:      # memory writes do not determine a signal value
            continue
        st, xs = _guards(a.guards, ev)
        xg += xs
        if st is not False:
            executed.append((i, a, st))
    full = [i for i, a, st in executed if st is True and not a.partial]
    if full:
        last = full[-1]
        eff = [a for i, a, st in executed if i >= last or a.partial]
    else:
        eff = [a for _, a, _ in executed]
    return eff, xg, bool(executed)


# ------------------------------------------------------------------ helpers
def _uniq(xs: list) -> list:
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _fmt(v: Optional[str]) -> str:
    if v is None:
        return "?"
    if v and all(c in "01xzXZ" for c in v):
        return f"{len(v)}'b{v}"
    return v


def _xz(v: Optional[str]) -> str:
    if v is None:
        return "X"
    s = v.lower()
    if "z" in s and "x" not in s:
        return "Z (undriven)"
    return "X/Z" if "z" in s else "X"


def _leaf(p: Optional[str]) -> str:
    return (p or "?").rsplit(".", 1)[-1]


def _where(d: Optional[Driver]) -> str:
    if d is None or not d.file:
        return ""
    return f" ({d.kind} at {d.file}:{d.line})"


# ------------------------------------------------------------------ public API
def trace(graph: DesignGraph, wave: WaveSource, signal: str, time: int, depth: int = 12) -> XTraceNode:
    """Trace the X on ``signal`` at ``time`` (waveform ticks) back to its root cause(s)."""
    return Tracer(graph, wave).trace(signal, int(time), int(depth))


def find_wave_run(db, run_id: Optional[str] = None):
    """The sim Run to trace and its waveform file: ``run_id`` or the latest sim run with waves."""
    runs = [db.get_run(run_id)] if run_id else db.list_runs(kind="sim", limit=500)
    for r in runs:
        f = wave_file(db, r)
        if f is not None:
            return r, f
    if run_id:
        raise LookupError(f"run {run_id} has no waveform artifact")
    raise LookupError("no simulation run with a waveform found (run `svkit sim <test>` with waves enabled)")


def wave_file(db, r) -> Optional[Path]:
    names = []
    m = r.metrics or {}
    if m.get("waves"):
        names.append(m["waves"])
    names += [n for n in r.artifacts if n.lower().endswith((".vcd", ".fst", ".vcd.gz"))]
    for n in names:
        rel = r.artifacts.get(n)
        if rel is None:
            continue
        f = (db.runs_dir / r.id / rel)
        if f.is_file():
            return f
    return None


def trace_run(cfg, db, *, run_id: Optional[str] = None, signal: str, time: int, depth: int = 12) -> XTraceNode:
    from svkit.design import graph as G
    from svkit.design.wavesrc import open_waves

    _, f = find_wave_run(db, run_id)
    g = G.build(cfg)
    return trace(g, open_waves(f), signal, time, depth)


def leaves(node: XTraceNode) -> list[XTraceNode]:
    """Root-cause leaves (nodes with a cause) of a trace tree."""
    out = []
    if node.cause is not None:
        out.append(node)
    for c in node.children:
        out += leaves(c)
    return out


def print_tree(node: XTraceNode, echo: Callable[[str], Any] = print) -> None:
    """Pretty-print the causality tree, symptom first, root causes marked."""
    def drv(n: XTraceNode) -> str:
        d = n.driver
        if d is None:
            return ""
        loc = f"{d.file}:{d.line}" if d.file else ""
        txt = (d.text or "")[:70]
        return f"  [{d.kind}{' ' + loc if loc else ''}{'  ' + txt if txt else ''}]"

    def rec(n: XTraceNode, prefix: str, last: bool, top: bool) -> None:
        conn = "" if top else ("└─ " if last else "├─ ")
        echo(f"{prefix}{conn}{n.signal} @{n.time} = {n.value}{drv(n)}")
        sub = prefix + ("" if top else ("   " if last else "│  "))
        if n.cause is not None:
            echo(f"{sub}{'   ' if n.children else ''}=> ROOT CAUSE [{n.cause.value}] {n.note or ''}".rstrip())
        elif n.note:
            echo(f"{sub}   ({n.note})")
        for i, c in enumerate(n.children):
            rec(c, sub, i == len(n.children) - 1, False)

    rec(node, "", True, True)
    roots = leaves(node)
    if roots:
        echo("")
        echo(f"{len(roots)} root cause(s): " + "; ".join(f"{r.cause.value} at {r.signal} @{r.time}" for r in roots))
